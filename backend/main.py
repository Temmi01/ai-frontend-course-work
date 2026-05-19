from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import networkx as nx
import numpy as np
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator, model_validator

try:
    import torch
    from torch import nn
except ImportError:  # pragma: no cover - optional dependency
    torch = None
    nn = None




# PATHS, CONSTANTS, AND RUNTIME DEFAULTS

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
CACHE_DIR = BASE_DIR.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)
NOTEBOOK_HTTP_CACHE_DIR = BASE_DIR.parent / "notebooks" / "cache"
ROUTING_CONFIG_FILE = BASE_DIR / "config" / "routing_config.json"

CORE_CACHE_FILES = {
    "osm_kyiv_core.json",
    "osm_kyiv_plus5km.json",
}
FULL_MAP_CACHE_FILE = "osm_full_map.json"

COMMENTS_FILE = DATA_DIR / "comments.json"
HISTORY_FILE = DATA_DIR / "history.json"
USERS_FILE = DATA_DIR / "users.json"
ARTICLES_FILE = DATA_DIR / "articles.json"

MODEL_DIR = BASE_DIR / "models"
MODEL_DIR.mkdir(exist_ok=True)
MLP_MODEL_FILE = MODEL_DIR / "mlp.pt"
GNN_MODEL_FILE = MODEL_DIR / "gnn.pt"

ALGORITHM_PATTERN = r"^(astar|astar_manhattan|alt|mlp|gnn)$"

ALLOWED_HIGHWAY_TYPES = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "residential",
    "service",
    "unclassified",
    "living_street",
}

DEFAULT_SPEED_KMH = {
    "motorway": 90,
    "motorway_link": 60,
    "trunk": 80,
    "trunk_link": 50,
    "primary": 60,
    "primary_link": 45,
    "secondary": 50,
    "secondary_link": 40,
    "tertiary": 40,
    "tertiary_link": 35,
    "residential": 30,
    "service": 20,
    "unclassified": 30,
    "living_street": 20,
}

def clamp(value: float, low: float, high: float) -> float:
    """Clamp a numeric value to an inclusive [low, high] range."""
    return max(low, min(high, value))


# GENERIC HELPERS (TIME, JSON, CORS, AUTH UTILS)
def now_utc() -> datetime:
    return datetime.now(UTC)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "")
    if raw.strip():
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ]


def normalize_email(value: str) -> str:
    return value.strip().lower()


def hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "is_admin": bool(user.get("is_admin", False)),
        "created_at": user.get("created_at"),
    }


def parse_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    value = authorization.strip()
    if not value:
        return None
    parts = value.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return value




# API REQUEST SCHEMAS

class RouteRequest(BaseModel):
    start_node: str = Field(..., min_length=1)
    end_node: str = Field(..., min_length=1)
    algorithm: str = Field("astar", pattern=ALGORITHM_PATTERN)


class CommentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    text: str = Field(..., min_length=1, max_length=3000)

    @field_validator("name", "text")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field must not be empty")
        return cleaned


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=4, max_length=180)
    password: str = Field(..., min_length=6, max_length=200)
    name: str = Field(..., min_length=2, max_length=120)

    @model_validator(mode="before")
    @classmethod
    def fix_swapped_identity_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        raw_email = str(data.get("email", "")).strip()
        raw_name = str(data.get("name", "")).strip()
        if raw_name and "@" in raw_name and raw_email and "@" not in raw_email:
            data = dict(data)
            data["email"] = raw_name
            data["name"] = raw_email
        return data

    @field_validator("email")
    @classmethod
    def normalize_mail(cls, value: str) -> str:
        mail = normalize_email(value)
        if "@" not in mail:
            raise ValueError("Invalid email")
        return mail

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        name = value.strip()
        if "@" in name:
            raise ValueError("Name field must not contain an email")
        if len(name) < 2:
            raise ValueError("Name is too short")
        return name


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=4, max_length=180)
    password: str = Field(..., min_length=6, max_length=200)

    @field_validator("email")
    @classmethod
    def normalize_mail(cls, value: str) -> str:
        mail = normalize_email(value)
        if "@" not in mail:
            raise ValueError("Invalid email")
        return mail


class ArticleRequest(BaseModel):
    title: str = Field(..., min_length=4, max_length=240)
    body: str = Field(..., min_length=20, max_length=12000)
    image_url: str | None = Field(default=None, max_length=2_000_000)

    @field_validator("title", "body")
    @classmethod
    def strip_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Field must not be empty")
        return text

    @field_validator("image_url")
    @classmethod
    def normalize_image_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ArticleCommentRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=3000)
    name: str | None = Field(default=None, max_length=120)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Comment text must not be empty")
        return cleaned

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None




# ROUTING CORE SERVICE (GRAPH + SEARCH)
@dataclass(frozen=True)
class RoutingConfig:

    auto_fetch_osm: bool
    overpass_endpoint: str
    overpass_timeout_sec: int
    auto_fetch_margin_deg: float
    cache_cleanup_enabled: bool
    cache_keep_max_files: int
    notebook_cache_keep_max_files: int
    ml_penalty_min: float
    ml_penalty_max: float
    ml_penalty_bpr_blend: float

    @classmethod
    def from_file(cls, path: Path) -> "RoutingConfig":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            auto_fetch_osm=bool(data["auto_fetch_osm"]),
            overpass_endpoint=str(data["overpass_endpoint"]).strip(),
            overpass_timeout_sec=int(data["overpass_timeout_sec"]),
            auto_fetch_margin_deg=float(data["auto_fetch_margin_deg"]),
            cache_cleanup_enabled=bool(data["cache_cleanup_enabled"]),
            cache_keep_max_files=int(data["cache_keep_max_files"]),
            notebook_cache_keep_max_files=int(data["notebook_cache_keep_max_files"]),
            ml_penalty_min=float(data["ml_penalty_min"]),
            ml_penalty_max=float(data["ml_penalty_max"]),
            ml_penalty_bpr_blend=float(data["ml_penalty_bpr_blend"]),
        )


class RoutingService:
    # ----- lifecycle and model loading -----
    def __init__(self) -> None:
        self._graph_lock = Lock()
        self._init_config()
        self._init_state()
        self._init_models()
        self._init_graph()

    def _init_config(self) -> None:
        
        self.config = RoutingConfig.from_file(ROUTING_CONFIG_FILE)
        self.auto_fetch_osm = self.config.auto_fetch_osm
        self.overpass_endpoint = self.config.overpass_endpoint or "https://overpass-api.de/api/interpreter"
        self.overpass_timeout_sec = max(1, int(self.config.overpass_timeout_sec))
        self.auto_fetch_margin_deg = max(0.001, float(self.config.auto_fetch_margin_deg))
        
        self.cache_cleanup_enabled = self.config.cache_cleanup_enabled
        self.cache_keep_max_files = max(1, int(self.config.cache_keep_max_files))
        self.notebook_cache_keep_max_files = max(1, int(self.config.notebook_cache_keep_max_files))
        
        self.ml_penalty_min = clamp(float(self.config.ml_penalty_min), 0.0, 8.0)
        self.ml_penalty_max = clamp(float(self.config.ml_penalty_max), self.ml_penalty_min, 8.0)
        self.ml_penalty_bpr_blend = clamp(float(self.config.ml_penalty_bpr_blend), 0.0, 1.0)

    def _init_state(self) -> None:
        self.active_cache_file: Path | None = None
        self.graph_bbox: dict[str, float] | None = None
        self._cache_bbox_mem: dict[str, tuple[float, float, float, float] | None] = {}
        self._node_penalty_mean: dict[str, dict[int, float]] = {"mlp": {}, "gnn": {}}
        self.landmarks: list[int] = []
        self.alt_forward: dict[int, dict[int, float]] = {}
        self.alt_reverse: dict[int, dict[int, float]] = {}
        self.alt_ready = False

    def _init_models(self) -> None:
        self.model_load_errors: dict[str, str | None] = {"mlp": None, "gnn": None}
        self.model_paths: dict[str, Path] = {
            "mlp": MLP_MODEL_FILE,
            "gnn": GNN_MODEL_FILE,
        }
        self.model_edge_weights_ready: dict[str, bool] = {"mlp": False, "gnn": False}
        self.mlp_model = self._load_model("mlp", self.model_paths["mlp"])
        self.gnn_model = self._load_model("gnn", self.model_paths["gnn"])

    def _init_graph(self) -> None:
        self._run_cache_maintenance()
        self.graph_source = "unknown"
        self.graph = self._build_graph()

    def _load_model(self, model_key: str, path: Path) -> Any | None:
        if not path.exists():
            self.model_load_errors[model_key] = None
            return None
        
        if torch is None:
            self.model_load_errors[model_key] = (
                f"Model file {path.name} exists, but torch is not installed. "
                "Install dependencies from backend/requirements.txt."
            )
            return None
        
        try:
            try:
                model = torch.load(path, map_location="cpu", weights_only=False)
            except TypeError:
                model = torch.load(path, map_location="cpu")
            self.model_load_errors[model_key] = None
            return model
        
        except Exception as exc:  # pragma: no cover
            self.model_load_errors[model_key] = str(exc)
            return None

    def _refresh_models(self) -> None:
        self.model_paths["mlp"] = MLP_MODEL_FILE
        self.model_paths["gnn"] = GNN_MODEL_FILE

        mlp_was_none = self.mlp_model is None
        gnn_was_none = self.gnn_model is None

        if self.mlp_model is None and self.model_paths["mlp"].exists():
            self.mlp_model = self._load_model("mlp", self.model_paths["mlp"])
            self.model_edge_weights_ready["mlp"] = False
        if self.gnn_model is None and self.model_paths["gnn"].exists():
            self.gnn_model = self._load_model("gnn", self.model_paths["gnn"])
            self.model_edge_weights_ready["gnn"] = False

        if mlp_was_none and self.mlp_model is not None:
            self.model_edge_weights_ready["mlp"] = False
        if gnn_was_none and self.gnn_model is not None:
            self.model_edge_weights_ready["gnn"] = False

        if self.mlp_model is None:
            self.model_edge_weights_ready["mlp"] = False
        if self.gnn_model is None:
            self.model_edge_weights_ready["gnn"] = False

    @staticmethod
    def _as_numpy_array(value: Any) -> np.ndarray:
        return np.asarray(value, dtype=np.float64)

    @staticmethod
    def _extract_landmark_nodes(node_positions: list[tuple[int, float, float]]) -> list[int]:
        if not node_positions:
            return []
        min_sum = min(node_positions, key=lambda x: x[1] + x[2])[0]
        max_sum = max(node_positions, key=lambda x: x[1] + x[2])[0]
        min_diff = min(node_positions, key=lambda x: x[1] - x[2])[0]
        max_diff = max(node_positions, key=lambda x: x[1] - x[2])[0]

        uniq: list[int] = []
        for node_id in [min_sum, max_sum, min_diff, max_diff]:
            if node_id not in uniq:
                uniq.append(node_id)
        return uniq

    @staticmethod
    def _node_centrality_from_lat_lng(lat: float, lng: float) -> float:
        center_lat = 50.4501
        center_lng = 30.5234
        lat_scale = 0.20
        lng_scale = 0.25
        radial = math.hypot((lat - center_lat) / lat_scale, (lng - center_lng) / lng_scale)
        return max(0.15, 1.0 - min(1.0, radial))

    @staticmethod
    def _cyclical_time_features(dt_value: datetime) -> tuple[float, float, float, float]:
        minutes = dt_value.hour * 60 + dt_value.minute
        tod = minutes / (24.0 * 60.0)
        dow = dt_value.weekday() / 7.0
        return (
            float(math.sin(2.0 * math.pi * tod)),
            float(math.cos(2.0 * math.pi * tod)),
            float(math.sin(2.0 * math.pi * dow)),
            float(math.cos(2.0 * math.pi * dow)),
        )

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            result = float(value)
            if math.isfinite(result):
                return result
        except (TypeError, ValueError):
            pass
        return default

    def _calibrate_ml_edge_penalty(
        self,
        raw_penalty: float,  
        free_flow_time_s: float, 
        bpr_time_s: float,  
    ) -> float:
        free_flow = max(self._safe_float(free_flow_time_s, 1e-6), 1e-6)
        bpr = max(self._safe_float(bpr_time_s, free_flow), free_flow)
        model_penalty = max(0.0, self._safe_float(raw_penalty, 0.0))
        model_penalty = clamp(model_penalty, self.ml_penalty_min, self.ml_penalty_max)

        bpr_penalty = max(0.0, (bpr / free_flow) - 1.0)
        bpr_penalty = clamp(bpr_penalty, self.ml_penalty_min, self.ml_penalty_max)

        blended_penalty = (1.0 - self.ml_penalty_bpr_blend) * model_penalty + self.ml_penalty_bpr_blend * bpr_penalty
        blended_penalty = clamp(blended_penalty, self.ml_penalty_min, self.ml_penalty_max)
        return blended_penalty

    def _edge_time_from_penalty(
        self,
        penalty: float,  # Final calibrated edge penalty coefficient.
        free_flow_time_s: float,  # Baseline edge travel time without congestion (seconds).
    ) -> float:
        free_flow = max(self._safe_float(free_flow_time_s, 1e-6), 1e-6)
        edge_penalty = clamp(max(0.0, self._safe_float(penalty, 0.0)), self.ml_penalty_min, self.ml_penalty_max)
        return free_flow * (1.0 + edge_penalty)

    def _rebuild_node_penalty_mean(self, model_key: str) -> None:
        penalty_key = "mlp_penalty_coef" if model_key == "mlp" else "gnn_penalty_coef"
        sums: dict[int, float] = {}
        counts: dict[int, int] = {}
        for u, _, data in self.graph.edges(data=True):
            penalty = self._safe_float(data.get(penalty_key), 0.0)
            node_id = int(u)
            sums[node_id] = sums.get(node_id, 0.0) + penalty
            counts[node_id] = counts.get(node_id, 0) + 1

        means: dict[int, float] = {}
        for node_id, total in sums.items():
            count = max(1, counts.get(node_id, 1))
            means[node_id] = clamp(total / float(count), self.ml_penalty_min, self.ml_penalty_max)
        self._node_penalty_mean[model_key] = means

    @staticmethod
    def _build_pems_mlp_runtime(state_dict: dict[str, Any]) -> Any:
        if nn is None:
            raise ValueError("PEMS MLP requires torch.nn in runtime.")
        if not isinstance(state_dict, dict):
            raise ValueError("PEMS MLP payload is missing valid 'state_dict'.")

        class _PemsMLPRuntime(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.net = nn.Sequential(
                    nn.Linear(6, 64),
                    nn.ReLU(),
                    nn.Linear(64, 64),
                    nn.ReLU(),
                    nn.Linear(64, 1),
                )

            def forward(self, x: Any) -> Any:
                return self.net(x)

        runtime = _PemsMLPRuntime()

        load_attempts: list[dict[str, Any]] = [state_dict]
        if all(not str(key).startswith("net.") for key in state_dict.keys()):
            load_attempts.append({f"net.{key}": value for key, value in state_dict.items()})

        last_error: Exception | None = None
        for candidate in load_attempts:
            try:
                runtime.load_state_dict(candidate, strict=True)
                runtime.eval()
                return runtime
            except Exception as exc:  # pragma: no cover - fallback diagnostics
                last_error = exc

        raise ValueError(f"PEMS MLP payload has incompatible layer names: {last_error}")

    def _build_runtime_sparse_adj_norm(
        self, node_to_idx: dict[int, int]
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        node_count = len(node_to_idx)
        if node_count == 0:
            return (
                np.zeros(0, dtype=np.float64),
                np.zeros(0, dtype=np.int64),
                np.zeros(0, dtype=np.int64),
                np.zeros(0, dtype=np.float64),
            )

        lengths: list[float] = []
        pair_weight: dict[tuple[int, int], float] = {}
        for u, v, data in self.graph.edges(data=True):
            ui = node_to_idx[int(u)]
            vi = node_to_idx[int(v)]
            if ui == vi:
                continue
            length_m = self._safe_float(data.get("length_m"), 0.0)
            if length_m > 0.0:
                lengths.append(length_m)

        sigma = float(np.std(lengths)) if lengths else 1.0
        if sigma <= 1e-6:
            sigma = float(np.mean(lengths)) if lengths else 1.0
        if sigma <= 1e-6:
            sigma = 1.0

        for u, v, data in self.graph.edges(data=True):
            ui = node_to_idx[int(u)]
            vi = node_to_idx[int(v)]
            if ui == vi:
                continue
            length_m = self._safe_float(data.get("length_m"), 0.0)
            if length_m <= 0.0:
                continue
            weight = math.exp(-((length_m / sigma) ** 2))
            if weight <= 0.0:
                continue
            a, b = (ui, vi) if ui < vi else (vi, ui)
            prev = pair_weight.get((a, b))
            if prev is None or weight > prev:
                pair_weight[(a, b)] = weight

        degree = np.ones(node_count, dtype=np.float64)
        for (ui, vi), w in pair_weight.items():
            degree[ui] += w
            degree[vi] += w

        src_idx: list[int] = []
        dst_idx: list[int] = []
        edge_coeff: list[float] = []
        for (ui, vi), w in pair_weight.items():
            coeff = w / math.sqrt(max(degree[ui] * degree[vi], 1e-12))
            src_idx.extend([ui, vi])
            dst_idx.extend([vi, ui])
            edge_coeff.extend([coeff, coeff])

        self_coeff = 1.0 / np.maximum(degree, 1e-8)
        return (
            self_coeff.astype(np.float64),
            np.asarray(src_idx, dtype=np.int64),
            np.asarray(dst_idx, dtype=np.int64),
            np.asarray(edge_coeff, dtype=np.float64),
        )

    @staticmethod
    def _sparse_norm_propagate(
        features: np.ndarray,
        self_coeff: np.ndarray,
        src_idx: np.ndarray,
        dst_idx: np.ndarray,
        edge_coeff: np.ndarray,
    ) -> np.ndarray:
        """Apply one sparse normalized message-passing propagation step."""
        propagated = self_coeff[:, None] * features
        if src_idx.size > 0:
            np.add.at(propagated, src_idx, edge_coeff[:, None] * features[dst_idx])
        return propagated

    def _ensure_model_edge_weights(self, model_key: str) -> None:
        """Compute and cache per-edge ML penalties/time for selected model family."""
        if self.graph.number_of_edges() == 0 or self.model_edge_weights_ready.get(model_key, False):
            return

        try:
            if model_key == "mlp":
                model = self.mlp_model
                if not isinstance(model, dict):
                    raise ValueError("MLP model file has unsupported format.")
                model_type = str(model.get("model_type", "")).strip().lower()
                if model_type != "pems_mlp_penalty_regressor":
                    raise ValueError(
                        f"Unsupported MLP model format: model_type='{model_type}'. "
                        "Expected 'pems_mlp_penalty_regressor'."
                    )
                if torch is None:
                    raise ValueError("PEMS MLP requires torch package in runtime.")

                state_dict = model.get("state_dict")
                feature_names = [str(x) for x in model.get("feature_names", [])]
                if not isinstance(state_dict, dict):
                    raise ValueError("PEMS MLP payload is missing 'state_dict'.")
                if len(feature_names) != 6:
                    raise ValueError("PEMS MLP payload has unexpected feature_names.")
                if "target_mean" not in model or "target_std" not in model:
                    raise ValueError(
                        "PEMS MLP penalty model is missing target normalization keys "
                        "('target_mean', 'target_std'). Re-export models from notebook."
                    )
                # Support both naming conventions from notebook exports:
                # 1) Sequential style: net.0/net.2/net.4
                # 2) Legacy dense style: linear1/linear2/(optional output layer)
                state_keys = {str(key) for key in state_dict.keys()}
                has_seq_layers = {"net.0.weight", "net.0.bias", "net.2.weight", "net.2.bias"}.issubset(state_keys)
                has_legacy_layers = {"linear1.weight", "linear1.bias", "linear2.weight", "linear2.bias"}.issubset(
                    state_keys
                )
                if not (has_seq_layers or has_legacy_layers):
                    raise ValueError(
                        "PEMS MLP payload has incompatible layer names. "
                        "Expected either net.0/net.2/... or linear1/linear2/... style keys."
                    )

                speed_mean = self._safe_float(model.get("speed_mean"), 0.0)
                speed_std = max(self._safe_float(model.get("speed_std"), 1.0), 1e-6)
                occ_mean = self._safe_float(model.get("occ_mean"), 0.0)
                occ_std = max(self._safe_float(model.get("occ_std"), 1.0), 1e-6)
                target_mean = self._safe_float(model.get("target_mean"), 0.0)
                target_std = max(self._safe_float(model.get("target_std"), 1.0), 1e-6)
                tod_sin, tod_cos, dow_sin, dow_cos = self._cyclical_time_features(now_utc())

                mlp_runtime = self._build_pems_mlp_runtime(state_dict)

                edges = list(self.graph.edges(data=True))
                X_rows: list[list[float]] = []
                for u, v, data in edges:
                    length_m = self._safe_float(data.get("length_m"), 0.0)
                    free_flow = max(self._safe_float(data.get("free_flow_time"), 1.0), 1e-6)
                    speed_mps = length_m / free_flow if length_m > 0 else 0.0
                    speed_mph = speed_mps / 0.44704

                    lat_u = self._safe_float(self.graph.nodes[u].get("lat"), 50.45)
                    lng_u = self._safe_float(self.graph.nodes[u].get("lng"), 30.52)
                    centrality = self._safe_float(data.get("centrality"), self._node_centrality_from_lat_lng(lat_u, lng_u))
                    occ_proxy = 0.02 + 0.18 * max(0.0, min(1.0, 1.0 - centrality))

                    row = [
                        (speed_mph - speed_mean) / speed_std,
                        (occ_proxy - occ_mean) / occ_std,
                        tod_sin,
                        tod_cos,
                        dow_sin,
                        dow_cos,
                    ]
                    X_rows.append(row)

                X_tensor = torch.tensor(np.array(X_rows, dtype=np.float32))
                with torch.no_grad():
                    pred_norm = mlp_runtime(X_tensor).squeeze(-1).cpu().numpy()
                pred_penalty = np.clip(pred_norm * target_std + target_mean, 0.0, None)

                for (_, _, data), raw_penalty in zip(edges, pred_penalty):
                    free_flow_time = max(self._safe_float(data.get("free_flow_time"), 1e-6), 1e-6)
                    bpr_time = max(self._safe_float(data.get("bpr_time"), free_flow_time), free_flow_time)
                    penalty = self._calibrate_ml_edge_penalty(float(raw_penalty), free_flow_time, bpr_time)
                    mlp_time = self._edge_time_from_penalty(penalty, free_flow_time)
                    data["mlp_penalty_coef"] = penalty
                    data["mlp_penalty_pred"] = penalty
                    data["mlp_time"] = mlp_time

                self._rebuild_node_penalty_mean("mlp")
                self.model_edge_weights_ready["mlp"] = True
                self.model_load_errors["mlp"] = None
                return

            if model_key == "gnn":
                model = self.gnn_model
                if not isinstance(model, dict):
                    raise ValueError("GNN model file has unsupported format.")
                model_type = str(model.get("model_type", "")).strip().lower()
                if model_type != "pems_gcn_penalty_regressor":
                    raise ValueError(
                        f"Unsupported GNN model format: model_type='{model_type}'. "
                        "Expected 'pems_gcn_penalty_regressor'."
                    )

                state_dict = model.get("state_dict")
                feature_names = [str(x) for x in model.get("feature_names", [])]
                if not isinstance(state_dict, dict):
                    raise ValueError("PEMS GCN payload is missing 'state_dict'.")
                if len(feature_names) != 6:
                    raise ValueError("PEMS GCN payload has unexpected feature_names.")
                if "target_mean" not in model or "target_std" not in model:
                    raise ValueError(
                        "PEMS GCN penalty model is missing target normalization keys "
                        "('target_mean', 'target_std'). Re-export models from notebook."
                    )
                gcn_required = {"lin1.weight", "lin1.bias", "lin2.weight", "lin2.bias", "out.weight", "out.bias"}
                gcn_missing = gcn_required.difference(state_dict.keys())
                if gcn_missing:
                    raise ValueError(f"PEMS GCN payload is missing layers: {sorted(gcn_missing)}")

                speed_mean = self._safe_float(model.get("speed_mean"), 0.0)
                speed_std = max(self._safe_float(model.get("speed_std"), 1.0), 1e-6)
                occ_mean = self._safe_float(model.get("occ_mean"), 0.0)
                occ_std = max(self._safe_float(model.get("occ_std"), 1.0), 1e-6)
                target_mean = self._safe_float(model.get("target_mean"), 0.0)
                target_std = max(self._safe_float(model.get("target_std"), 1.0), 1e-6)
                tod_sin, tod_cos, dow_sin, dow_cos = self._cyclical_time_features(now_utc())

                node_ids = [int(node_id) for node_id in self.graph.nodes]
                node_to_idx = {node_id: idx for idx, node_id in enumerate(node_ids)}
                N = len(node_ids)
                if N == 0:
                    self.model_edge_weights_ready["gnn"] = True
                    return

                node_speed_mph = np.zeros(N, dtype=np.float64)
                node_occ_proxy = np.zeros(N, dtype=np.float64)
                speed_count = np.zeros(N, dtype=np.float64)
                for u, v, data in self.graph.edges(data=True):
                    ui = node_to_idx[int(u)]
                    vi = node_to_idx[int(v)]
                    length_m = self._safe_float(data.get("length_m"), 0.0)
                    free_flow = max(self._safe_float(data.get("free_flow_time"), 1.0), 1e-6)
                    speed_mph = (length_m / free_flow) / 0.44704 if length_m > 0 else 25.0
                    node_speed_mph[ui] += speed_mph
                    node_speed_mph[vi] += speed_mph
                    speed_count[ui] += 1.0
                    speed_count[vi] += 1.0

                    lat_u = self._safe_float(self.graph.nodes[u].get("lat"), 50.45)
                    lng_u = self._safe_float(self.graph.nodes[u].get("lng"), 30.52)
                    cent_u = self._safe_float(data.get("centrality"), self._node_centrality_from_lat_lng(lat_u, lng_u))
                    lat_v = self._safe_float(self.graph.nodes[v].get("lat"), 50.45)
                    lng_v = self._safe_float(self.graph.nodes[v].get("lng"), 30.52)
                    cent_v = self._safe_float(data.get("centrality"), self._node_centrality_from_lat_lng(lat_v, lng_v))
                    node_occ_proxy[ui] += 0.02 + 0.18 * max(0.0, min(1.0, 1.0 - cent_u))
                    node_occ_proxy[vi] += 0.02 + 0.18 * max(0.0, min(1.0, 1.0 - cent_v))

                speed_count = np.maximum(speed_count, 1.0)
                node_speed_mph /= speed_count
                node_occ_proxy /= speed_count

                X_nodes = np.empty((N, 6), dtype=np.float32)
                X_nodes[:, 0] = ((node_speed_mph - speed_mean) / speed_std).astype(np.float32)
                X_nodes[:, 1] = ((node_occ_proxy - occ_mean) / occ_std).astype(np.float32)
                X_nodes[:, 2] = np.float32(tod_sin)
                X_nodes[:, 3] = np.float32(tod_cos)
                X_nodes[:, 4] = np.float32(dow_sin)
                X_nodes[:, 5] = np.float32(dow_cos)

                self_coeff, src_idx, dst_idx, edge_coeff = self._build_runtime_sparse_adj_norm(node_to_idx=node_to_idx)
                W1 = self._as_numpy_array(state_dict["lin1.weight"])
                b1 = self._as_numpy_array(state_dict["lin1.bias"]).reshape(1, -1)
                W2 = self._as_numpy_array(state_dict["lin2.weight"])
                b2 = self._as_numpy_array(state_dict["lin2.bias"]).reshape(1, -1)
                W_out = self._as_numpy_array(state_dict["out.weight"])
                b_out = self._as_numpy_array(state_dict["out.bias"]).reshape(1, -1)

                H0 = X_nodes.astype(np.float64)
                Z1 = self._sparse_norm_propagate(H0, self_coeff, src_idx, dst_idx, edge_coeff)
                H1 = np.maximum(0.0, Z1 @ W1.T + b1)
                Z2 = self._sparse_norm_propagate(H1, self_coeff, src_idx, dst_idx, edge_coeff)
                H2 = np.maximum(0.0, Z2 @ W2.T + b2)
                Z3 = self._sparse_norm_propagate(H2, self_coeff, src_idx, dst_idx, edge_coeff)
                pred_norm = (Z3 @ W_out.T + b_out).reshape(-1)
                pred_penalty = np.clip(pred_norm * target_std + target_mean, 0.0, None)

                for u, v, data in self.graph.edges(data=True):
                    ui = node_to_idx[int(u)]
                    vi = node_to_idx[int(v)]
                    raw_penalty = 0.5 * (float(pred_penalty[ui]) + float(pred_penalty[vi]))
                    free_flow_time = max(self._safe_float(data.get("free_flow_time"), 1e-6), 1e-6)
                    bpr_time = max(self._safe_float(data.get("bpr_time"), free_flow_time), free_flow_time)
                    penalty = self._calibrate_ml_edge_penalty(raw_penalty, free_flow_time, bpr_time)
                    gnn_time = self._edge_time_from_penalty(penalty, free_flow_time)
                    data["gnn_penalty_coef"] = penalty
                    data["gnn_penalty_pred"] = penalty
                    data["gnn_time"] = gnn_time

                self._rebuild_node_penalty_mean("gnn")
                self.model_edge_weights_ready["gnn"] = True
                self.model_load_errors["gnn"] = None
                return

            raise ValueError(f"Unsupported model key: {model_key}")
        except Exception as exc:
            self.model_edge_weights_ready[model_key] = False
            self.model_load_errors[model_key] = str(exc)

    def model_status(self) -> dict[str, dict[str, Any]]:
        """Return current model load status for diagnostics endpoint."""
        self._refresh_models()
        return {
            "mlp": {
                "loaded": self.mlp_model is not None,
                "path": str(self.model_paths["mlp"]),
                "file_exists": self.model_paths["mlp"].exists(),
                "load_error": self.model_load_errors.get("mlp"),
            },
            "gnn": {
                "loaded": self.gnn_model is not None,
                "path": str(self.model_paths["gnn"]),
                "file_exists": self.model_paths["gnn"].exists(),
                "load_error": self.model_load_errors.get("gnn"),
            },
        }

    @staticmethod
    def _is_supported_mlp_model(model: Any) -> bool:
        if not isinstance(model, dict):
            return False
        model_type = str(model.get("model_type", "")).strip().lower()
        return (
            model_type == "pems_mlp_penalty_regressor"
            and isinstance(model.get("state_dict"), dict)
            and len([str(x) for x in model.get("feature_names", [])]) == 6
            and ("target_mean" in model and "target_std" in model)
        )

    @staticmethod
    def _is_supported_gnn_model(model: Any) -> bool:
        if not isinstance(model, dict):
            return False
        model_type = str(model.get("model_type", "")).strip().lower()
        return (
            model_type == "pems_gcn_penalty_regressor"
            and isinstance(model.get("state_dict"), dict)
            and len([str(x) for x in model.get("feature_names", [])]) == 6
            and ("target_mean" in model and "target_std" in model)
        )

    def available_algorithms(self) -> list[str]:
        """Return algorithms available with currently loaded assets."""
        self._refresh_models()
        available = ["astar", "astar_manhattan", "alt"]
        if self.mlp_model is not None and self._is_supported_mlp_model(self.mlp_model):
            available.append("mlp")
        if self.gnn_model is not None and self._is_supported_gnn_model(self.gnn_model):
            available.append("gnn")
        return available

    def _ensure_algorithm_available(self, algorithm: str) -> None:
        """Validate algorithm availability and prepare ML edge weights when needed."""
        self._refresh_models()
        if algorithm == "mlp" and self.mlp_model is None:
            raise ValueError(
                "MLP algorithm is unavailable: model file is missing or failed to load. "
                f"Expected file: {self.model_paths['mlp']}"
            )
        if algorithm == "mlp" and not self._is_supported_mlp_model(self.mlp_model):
            raise ValueError(
                "MLP algorithm is unavailable: model format is unsupported. "
                "Export a new penalty model from notebook (model_type='pems_mlp_penalty_regressor')."
            )
        if algorithm == "gnn" and self.gnn_model is None:
            raise ValueError(
                "GNN algorithm is unavailable: model file is missing or failed to load. "
                f"Expected file: {self.model_paths['gnn']}"
            )
        if algorithm == "gnn" and not self._is_supported_gnn_model(self.gnn_model):
            raise ValueError(
                "GNN algorithm is unavailable: model format is unsupported. "
                "Export a new penalty model from notebook (model_type='pems_gcn_penalty_regressor')."
            )
        if algorithm == "mlp":
            self._ensure_model_edge_weights("mlp")
            if not self.model_edge_weights_ready.get("mlp", False):
                details = self.model_load_errors.get("mlp") or "unknown model inference error"
                raise ValueError(f"MLP edge weights are unavailable: {details}")
        if algorithm == "gnn":
            self._ensure_model_edge_weights("gnn")
            if not self.model_edge_weights_ready.get("gnn", False):
                details = self.model_load_errors.get("gnn") or "unknown model inference error"
                raise ValueError(f"GNN edge weights are unavailable: {details}")


    # GRAPH CONSTRUCTION
    def _build_graph(self) -> nx.DiGraph:
        """Build initial graph from OSM cache only."""
        startup_cache = self._find_startup_osm_cache()
        graph = self._build_graph_from_osm_cache(startup_cache)
        if graph.number_of_nodes() > 0 and graph.number_of_edges() > 0:
            self.graph_source = "osm_cache"
            return graph
        self.graph_source = "osm_unavailable"
        self.node_positions = []
        self.graph_bbox = None
        return nx.DiGraph()

    def _cache_has_osm_elements(self, path: Path) -> bool:
        """Return True when a JSON file looks like a valid Overpass response."""
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return isinstance(obj, dict) and isinstance(obj.get("elements"), list) and len(obj.get("elements")) > 0

    @staticmethod
    def _cache_bbox_from_meta(data: dict[str, Any]) -> tuple[float, float, float, float] | None:
        meta = data.get("meta")
        if not isinstance(meta, dict):
            return None
        bbox = meta.get("bbox")
        if not isinstance(bbox, dict):
            return None
        try:
            south = float(bbox.get("south"))
            west = float(bbox.get("west"))
            north = float(bbox.get("north"))
            east = float(bbox.get("east"))
        except (TypeError, ValueError):
            return None
        if south >= north or west >= east:
            return None
        return (south, west, north, east)

    @staticmethod
    def _compute_bbox_from_elements(elements: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
        lats: list[float] = []
        lngs: list[float] = []
        for element in elements:
            if element.get("type") != "node":
                continue
            if "lat" not in element or "lon" not in element:
                continue
            try:
                lat = float(element["lat"])
                lng = float(element["lon"])
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(lat) and math.isfinite(lng)):
                continue
            lats.append(lat)
            lngs.append(lng)
        if not lats or not lngs:
            return None
        return (min(lats), min(lngs), max(lats), max(lngs))

    def _read_cache_bbox(self, path: Path) -> tuple[float, float, float, float] | None:
        mem_key = str(path.resolve())
        if mem_key in self._cache_bbox_mem:
            return self._cache_bbox_mem[mem_key]

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self._cache_bbox_mem[mem_key] = None
            return None

        if not isinstance(raw, dict):
            self._cache_bbox_mem[mem_key] = None
            return None

        bbox = self._cache_bbox_from_meta(raw)
        if bbox is None:
            elements = raw.get("elements", [])
            if isinstance(elements, list):
                bbox = self._compute_bbox_from_elements(elements)
        self._cache_bbox_mem[mem_key] = bbox
        return bbox

    @staticmethod
    def _bbox_contains_point(bbox: tuple[float, float, float, float], lat: float, lng: float, pad: float = 0.0) -> bool:
        south, west, north, east = bbox
        return (south - pad) <= lat <= (north + pad) and (west - pad) <= lng <= (east + pad)

    def _bbox_covers_route(
        self,
        bbox: tuple[float, float, float, float],
        start_lat: float,
        start_lng: float,
        end_lat: float,
        end_lng: float,
        pad: float = 0.0,
    ) -> bool:
        return self._bbox_contains_point(bbox, start_lat, start_lng, pad=pad) and self._bbox_contains_point(
            bbox, end_lat, end_lng, pad=pad
        )

    @staticmethod
    def _bbox_area(bbox: tuple[float, float, float, float]) -> float:
        """Return simple rectangular area in degrees for cache prioritization."""
        south, west, north, east = bbox
        return max(0.0, north - south) * max(0.0, east - west)

    def _list_osm_cache_candidates(self) -> list[Path]:
        """List valid OSM cache files sorted by freshness and size."""
        if not CACHE_DIR.exists():
            return []
        candidates = sorted(
            [path for path in CACHE_DIR.rglob("*.json") if path.is_file()],
            key=lambda p: (p.stat().st_mtime, p.stat().st_size),
            reverse=True,
        )
        return [path for path in candidates if self._cache_has_osm_elements(path)]

    def _find_startup_osm_cache(self) -> Path | None:
        """Choose initial graph cache on startup (prefer kyiv core presets)."""
        if self.active_cache_file and self.active_cache_file.exists() and self._cache_has_osm_elements(self.active_cache_file):
            return self.active_cache_file

        candidates = self._list_osm_cache_candidates()
        if not candidates:
            return None

        for preferred in ("osm_kyiv_plus5km.json", "osm_kyiv_core.json"):
            for candidate in candidates:
                if candidate.name == preferred:
                    return candidate

        return candidates[0]

    def _find_covering_osm_cache(
        self,
        start_lat: float,
        start_lng: float,
        end_lat: float,
        end_lng: float,
        pad: float = 0.0,
    ) -> Path | None:
        """Find the smallest existing cache that already covers both route points."""
        candidates = self._list_osm_cache_candidates()
        if not candidates:
            return None

        scored: list[tuple[float, float, Path]] = []
        for candidate in candidates:
            bbox = self._read_cache_bbox(candidate)
            if bbox is None:
                continue
            if not self._bbox_covers_route(bbox, start_lat, start_lng, end_lat, end_lng, pad=pad):
                continue
            area = self._bbox_area(bbox)
            mtime = candidate.stat().st_mtime
            scored.append((area, -mtime, candidate))

        if not scored:
            return None
        scored.sort(key=lambda item: (item[0], item[1]))
        return scored[0][2]

    def _cleanup_json_cache_dir(
        self,
        target_dir: Path,
        *,
        keep_max_files: int,
        protected_names: set[str] | None = None,
    ) -> None:
        """Prune cache JSON files by count while preserving protected files."""
        if not target_dir.exists():
            return

        protected = protected_names or set()
        files = [path for path in target_dir.rglob("*.json") if path.is_file()]
        if not files:
            return

        now = time.time()
        keep_set: set[Path] = set()
        if self.active_cache_file and self.active_cache_file.exists() and self.active_cache_file.parent == target_dir:
            keep_set.add(self.active_cache_file)
        keep_set.update(path for path in files if path.name in protected)

        files = [path for path in target_dir.rglob("*.json") if path.is_file()]
        if keep_max_files <= 0 or len(files) <= keep_max_files:
            return

        removable = [path for path in files if path not in keep_set]
        removable.sort(key=lambda p: p.stat().st_mtime)
        need_remove = max(0, len(files) - keep_max_files)
        for path in removable[:need_remove]:
            try:
                path.unlink(missing_ok=True)
                self._cache_bbox_mem.pop(str(path.resolve()), None)
            except OSError:
                pass

    def _run_cache_maintenance(self) -> None:
        """Run periodic cleanup for route caches and notebook HTTP cache."""
        if not self.cache_cleanup_enabled:
            return
        self._cleanup_json_cache_dir(
            CACHE_DIR,
            keep_max_files=max(6, self.cache_keep_max_files),
            protected_names=CORE_CACHE_FILES,
        )
        self._cleanup_json_cache_dir(
            NOTEBOOK_HTTP_CACHE_DIR,
            keep_max_files=max(50, self.notebook_cache_keep_max_files),
            protected_names=set(),
        )

    def _build_overpass_query(self, south: float, west: float, north: float, east: float) -> str:
        """Build Overpass query filtered to allowed highway classes only."""
        highway_pattern = "|".join(sorted(ALLOWED_HIGHWAY_TYPES))
        return f"""
[out:json][timeout:{self.overpass_timeout_sec}];
(
  way["highway"~"^({highway_pattern})$"]({south},{west},{north},{east});
);
(._;>;);
out body;
""".strip()

    def _download_osm_cache_for_bbox(self, south: float, west: float, north: float, east: float) -> Path | None:
        """Download one bbox graph from Overpass and store it as route cache JSON."""
        query = self._build_overpass_query(south, west, north, east)
        payload = urlencode({"data": query}).encode("utf-8")
        request = Request(
            self.overpass_endpoint,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                "User-Agent": "ai-navigation-backend/1.0",
            },
        )

        try:
            with urlopen(request, timeout=max(15, self.overpass_timeout_sec + 10)) as response:
                raw = response.read().decode("utf-8")
        except URLError:
            return None
        except TimeoutError:
            return None
        except Exception:
            return None

        try:
            data = json.loads(raw)
        except Exception:
            return None

        elements = data.get("elements", []) if isinstance(data, dict) else []
        if not isinstance(elements, list) or len(elements) == 0:
            return None

        if isinstance(data, dict):
            meta = data.get("meta")
            if not isinstance(meta, dict):
                meta = {}
            meta["bbox"] = {
                "south": float(south),
                "west": float(west),
                "north": float(north),
                "east": float(east),
            }
            meta["saved_at_utc"] = datetime.now(UTC).isoformat()
            meta["source"] = "overpass_auto_route"
            data["meta"] = meta

        timestamp = int(time.time())
        file_name = f"osm_auto_{timestamp}_{south:.4f}_{west:.4f}_{north:.4f}_{east:.4f}.json".replace("-", "m")
        output_path = CACHE_DIR / file_name
        output_path.write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        self.active_cache_file = output_path
        bbox_mem_key = str(output_path.resolve())
        self._cache_bbox_mem[bbox_mem_key] = (float(south), float(west), float(north), float(east))
        self._run_cache_maintenance()
        return output_path

    def _speed_mps(self, tags: dict[str, Any]) -> float:
        """Estimate speed in m/s from OSM tags with robust highway fallback."""
        highway_type = str(tags.get("highway", "residential")).strip().lower()
        fallback_kmh = DEFAULT_SPEED_KMH.get(highway_type, 30)

        raw = str(tags.get("maxspeed", "")).strip()
        if raw:
            match = re.search(r"\d+(?:\.\d+)?", raw)
            if match:
                kmh = float(match.group(0))
                return max(3.0, kmh / 3.6)

        return max(3.0, fallback_kmh / 3.6)

    def _is_oneway(self, tags: dict[str, Any]) -> bool:
        """Detect one-way direction from OSM tags."""
        value = str(tags.get("oneway", "")).strip().lower()
        if value in {"yes", "1", "true"}:
            return True
        if value == "-1":
            return True
        if str(tags.get("junction", "")).strip().lower() == "roundabout":
            return True

        highway_type = str(tags.get("highway", "")).strip().lower()
        return highway_type in {"motorway", "motorway_link"}

    def haversine_m(self, start_lat: float, start_lng: float, end_lat: float, end_lng: float) -> float:
        """Compute great-circle distance in meters between two coordinates."""
        radius = 6_371_000.0
        p1 = math.radians(start_lat)
        p2 = math.radians(end_lat)
        dp = math.radians(end_lat - start_lat)
        dl = math.radians(end_lng - start_lng)

        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return radius * c

    def bpr_time(
        self,
        free_flow_time_s: float,  # Edge travel time in free-flow conditions (seconds).
        volume: float,  # Current traffic demand proxy for this edge.
        capacity: float,  # Edge capacity proxy (vehicles/hour-like synthetic units).
        alpha: float = 0.15,  # BPR scaling coefficient.
        beta: float = 4.0,  # BPR nonlinearity exponent.
    ) -> float:
        """Compute BPR congestion-adjusted travel time."""
        ratio = max(0.0, volume / max(capacity, 1e-6))
        return free_flow_time_s * (1.0 + alpha * (ratio**beta))

    def _attach_edge_metrics(
        self,
        graph: nx.DiGraph,  # Target graph where the directed edge is created/updated.
        start_node_id: int,  # Source node id.
        end_node_id: int,  # Destination node id.
        length_m: float,  # Edge geometry length (meters).
        highway_type: str,  # OSM road class used for default speed priors.
        existing_free_flow_time: float | None = None,  # Optional precomputed free-flow time (seconds).
    ) -> None:
        """Attach routing metrics and synthetic congestion priors to one directed edge."""
        if existing_free_flow_time is not None and existing_free_flow_time > 0.0:
            free_flow_time = float(existing_free_flow_time)
            speed_mps = length_m / max(free_flow_time, 1e-6)
        else:
            speed_mps = max(3.0, DEFAULT_SPEED_KMH.get(highway_type, 30) / 3.6)
            free_flow_time = length_m / speed_mps

        lat_u = float(graph.nodes[start_node_id]["lat"])
        lng_u = float(graph.nodes[start_node_id]["lng"])
        centrality = self._node_centrality_from_lat_lng(lat_u, lng_u)

        capacity = 850.0 + 900.0 * centrality
        volume = capacity * (0.52 + 0.88 * centrality)
        bpr_time = self.bpr_time(free_flow_time, volume, capacity)

        edge_payload: dict[str, float | str] = {
            "length_m": length_m,
            "free_flow_time": free_flow_time,
            "speed_kph": speed_mps * 3.6,
            "capacity": capacity,
            "volume": volume,
            "bpr_time": bpr_time,
            "centrality": centrality,
            "highway_type": highway_type,
        }

        graph.add_edge(start_node_id, end_node_id, **edge_payload)

    def _build_spatial_index(self, graph: nx.DiGraph) -> None:
        """Build node coordinate index and graph bbox for quick point checks."""
        self.node_positions: list[tuple[int, float, float]] = [
            (int(node_id), float(data["lat"]), float(data["lng"]))
            for node_id, data in graph.nodes(data=True)
            if "lat" in data and "lng" in data
        ]
        self.node_component: dict[int, int] = {}
        if not self.node_positions:
            self.graph_bbox = None
            return

        lats = [item[1] for item in self.node_positions]
        lngs = [item[2] for item in self.node_positions]
        self.graph_bbox = {
            "south": min(lats),
            "west": min(lngs),
            "north": max(lats),
            "east": max(lngs),
        }
        for comp_idx, component_nodes in enumerate(nx.weakly_connected_components(graph)):
            for node_id in component_nodes:
                self.node_component[int(node_id)] = int(comp_idx)


    # OSM CACHE INGESTION
    def _build_graph_from_osm_cache(self, cache_file: Path | None = None) -> nx.DiGraph:
        """Build routing graph from one OSM cache file and enrich edge metrics."""
        if cache_file is None:
            cache_file = self._find_startup_osm_cache()
        if cache_file is None:
            return nx.DiGraph()

        try:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            return nx.DiGraph()

        elements = raw.get("elements", []) if isinstance(raw, dict) else []
        node_coords: dict[int, tuple[float, float]] = {}

        for element in elements:
            if element.get("type") == "node" and "lat" in element and "lon" in element:
                node_coords[int(element["id"])] = (float(element["lat"]), float(element["lon"]))

        graph = nx.DiGraph()
        for node_id, (lat, lng) in node_coords.items():
            graph.add_node(node_id, lat=lat, lng=lng)

        for element in elements:
            if element.get("type") != "way":
                continue

            tags = element.get("tags") or {}
            highway_type = str(tags.get("highway", "")).strip().lower()
            if highway_type not in ALLOWED_HIGHWAY_TYPES:
                continue

            way_nodes = element.get("nodes") or []
            if len(way_nodes) < 2:
                continue

            speed_mps = self._speed_mps(tags)
            one_way = self._is_oneway(tags)
            reverse_one_way = str(tags.get("oneway", "")).strip() == "-1"

            for a, b in zip(way_nodes[:-1], way_nodes[1:]):
                u = int(a)
                v = int(b)
                if u not in node_coords or v not in node_coords:
                    continue

                start_lat, start_lng = node_coords[u]
                end_lat, end_lng = node_coords[v]
                length_m = self.haversine_m(start_lat, start_lng, end_lat, end_lng)
                if length_m <= 0:
                    continue

                free_flow_time = length_m / speed_mps
                graph.add_edge(
                    u,
                    v,
                    length_m=length_m,
                    free_flow_time=free_flow_time,
                    highway_type=highway_type,
                )

                if reverse_one_way:
                    graph.remove_edge(u, v)
                    graph.add_edge(
                        v,
                        u,
                        length_m=length_m,
                        free_flow_time=free_flow_time,
                        highway_type=highway_type,
                    )
                    continue

                if not one_way:
                    graph.add_edge(
                        v,
                        u,
                        length_m=length_m,
                        free_flow_time=free_flow_time,
                        highway_type=highway_type,
                    )

        if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
            return nx.DiGraph()

        # Keep all weakly connected components.
        # Previously we kept only the largest component, which could drop local
        # residential segments and cause large snapping jumps for selected points.

        for u, v, data in list(graph.edges(data=True)):
            length_m = float(data.get("length_m", 0.0))
            if length_m <= 0:
                continue
            self._attach_edge_metrics(
                graph,
                int(u),
                int(v),
                length_m,
                str(data.get("highway_type", "residential")),
                existing_free_flow_time=self._safe_float(data.get("free_flow_time"), 0.0),
            )

        self.active_cache_file = cache_file
        self._build_spatial_index(graph)
        return graph


    # ALT LANDMARK PRECOMPUTATION
    def prepare_alt_landmarks(self, weight_key: str = "free_flow_time") -> None:
        """Precompute ALT landmark distance tables for fast lower bounds."""
        if self.alt_ready:
            return

        if not self.node_positions:
            self.alt_ready = True
            self.landmarks = []
            return

        self.landmarks = self._extract_landmark_nodes(self.node_positions)
        self.alt_forward = {}
        self.alt_reverse = {}

        reverse_graph = self.graph.reverse(copy=False)
        for landmark in self.landmarks:
            self.alt_forward[landmark] = nx.single_source_dijkstra_path_length(
                self.graph,
                landmark,
                weight=weight_key,
            )
            self.alt_reverse[landmark] = nx.single_source_dijkstra_path_length(
                reverse_graph,
                landmark,
                weight=weight_key,
            )

        self.alt_ready = True

    # INPUT RESOLUTION
    def _nearest_node(self, lat: float, lng: float) -> int:
        """Find nearest graph node by geodesic (haversine) distance in meters."""
        if not self.node_positions:
            raise ValueError("Graph does not have a spatial index.")
        best_node = self.node_positions[0][0]
        best_score = float("inf")
        for node_id, nlat, nlng in self.node_positions:
            score = self.haversine_m(lat, lng, nlat, nlng)
            if score < best_score:
                best_score = score
                best_node = node_id
        return best_node

    def _nearest_node_candidates(self, lat: float, lng: float, limit: int = 30) -> list[tuple[int, float]]:
        """Return top-N nearest node candidates as (node_id, distance_m)."""
        if not self.node_positions:
            return []
        rows: list[tuple[float, int]] = []
        for node_id, nlat, nlng in self.node_positions:
            rows.append((self.haversine_m(lat, lng, nlat, nlng), int(node_id)))
        rows.sort(key=lambda item: item[0])
        top = rows[: max(1, int(limit))]
        return [(node_id, float(dist_m)) for dist_m, node_id in top]

    def _nearest_node_distance_m(self, lat: float, lng: float) -> float:
        """Return distance in meters to nearest graph node for a coordinate."""
        top = self._nearest_node_candidates(lat, lng, limit=1)
        if not top:
            return float("inf")
        return float(top[0][1])

    def _try_parse_lat_lng(self, text: str) -> tuple[float, float] | None:
        """Parse 'lat,lng' or 'lat lng' input format into numeric tuple."""
        if not text:
            return None

        parts = re.split(r"[ ,]+", text.strip())
        if len(parts) < 2:
            return None
        try:
            lat = float(parts[0])
            lng = float(parts[1])
        except ValueError:
            return None
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return None
        return lat, lng

    def _point_inside_graph_bbox(self, lat: float, lng: float, margin: float = 0.0) -> bool:
        """Check whether point is inside currently loaded graph bbox."""
        if not self.graph_bbox:
            return False
        return (
            self.graph_bbox["south"] - margin <= lat <= self.graph_bbox["north"] + margin
            and self.graph_bbox["west"] - margin <= lng <= self.graph_bbox["east"] + margin
        )

    def _rebuild_graph_from_best_cache(self, cache_path: Path | None = None) -> bool:
        """Rebuild active graph from selected cache and reset derived states."""
        graph = self._build_graph_from_osm_cache(cache_path)
        if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
            return False

        self.graph = graph
        self.graph_source = "osm_cache"
        self.alt_ready = False
        self.landmarks = []
        self.alt_forward = {}
        self.alt_reverse = {}
        self.model_edge_weights_ready["mlp"] = False
        self.model_edge_weights_ready["gnn"] = False
        self._node_penalty_mean = {"mlp": {}, "gnn": {}}
        return True

    def _ensure_graph_for_route_points(self, start_node_input: str, end_node_input: str) -> None:
        """Ensure active graph covers both route points; reuse cache or fetch new bbox."""
        start_coords = self._try_parse_lat_lng(start_node_input)
        end_coords = self._try_parse_lat_lng(end_node_input)
        if start_coords is None or end_coords is None:
            return

        start_lat, start_lng = start_coords
        end_lat, end_lng = end_coords
        margin = max(0.005, self.auto_fetch_margin_deg)
        max_snap_m = 180.0

        with self._graph_lock:
            self._run_cache_maintenance()
            start_inside = self._point_inside_graph_bbox(start_lat, start_lng, margin=0.003)
            end_inside = self._point_inside_graph_bbox(end_lat, end_lng, margin=0.003)
            if self.graph_source == "osm_cache" and start_inside and end_inside:
                start_snap_m = self._nearest_node_distance_m(start_lat, start_lng)
                end_snap_m = self._nearest_node_distance_m(end_lat, end_lng)
                if start_snap_m <= max_snap_m and end_snap_m <= max_snap_m:
                    return

            best_existing = self._find_covering_osm_cache(
                start_lat,
                start_lng,
                end_lat,
                end_lng,
                pad=0.0015,
            )
            if best_existing is not None:
                self._rebuild_graph_from_best_cache(best_existing)
                start_snap_m = self._nearest_node_distance_m(start_lat, start_lng)
                end_snap_m = self._nearest_node_distance_m(end_lat, end_lng)
                if start_snap_m <= max_snap_m and end_snap_m <= max_snap_m:
                    return

            # If compact Kyiv caches are too sparse near selected points,
            # try dense full-map cache before making network requests.
            full_map_cache = CACHE_DIR / FULL_MAP_CACHE_FILE
            if full_map_cache.exists():
                self._rebuild_graph_from_best_cache(full_map_cache)
                start_snap_m = self._nearest_node_distance_m(start_lat, start_lng)
                end_snap_m = self._nearest_node_distance_m(end_lat, end_lng)
                if start_snap_m <= max_snap_m and end_snap_m <= max_snap_m:
                    return

            if not self.auto_fetch_osm:
                return

            south = clamp(min(start_lat, end_lat) - margin, -89.9, 89.9)
            north = clamp(max(start_lat, end_lat) + margin, -89.9, 89.9)
            west = clamp(min(start_lng, end_lng) - margin, -179.9, 179.9)
            east = clamp(max(start_lng, end_lng) + margin, -179.9, 179.9)
            south = round(south, 4)
            north = round(north, 4)
            west = round(west, 4)
            east = round(east, 4)

            if south >= north or west >= east:
                return

            downloaded = self._download_osm_cache_for_bbox(south, west, north, east)
            if downloaded is None:
                return

            self._rebuild_graph_from_best_cache(downloaded)

    def _parse_node_input(self, node_input: str) -> int:
        """Resolve node input as coordinates, explicit node id, or deterministic fallback."""
        if not node_input:
            raise ValueError("Route point is not provided.")

        parsed_coords = self._try_parse_lat_lng(node_input)
        if parsed_coords is not None:
            return self._nearest_node(parsed_coords[0], parsed_coords[1])

        try:
            node_id = int(node_input.strip())
            if node_id in self.graph:
                return node_id
        except ValueError:
            pass

        nodes = [node_id for node_id, _, _ in self.node_positions]
        if not nodes:
            raise ValueError("Routing graph contains no nodes.")
        return nodes[abs(hash(node_input)) % len(nodes)]

    def _resolve_route_node_pair(self, start_node_input: str, end_node_input: str) -> tuple[int, int]:
        """
        Resolve both route endpoints with component-aware snapping.
        When both inputs are coordinates, choose nearest pair that belongs
        to the same weakly-connected component to avoid disconnected snaps.
        """
        start_coords = self._try_parse_lat_lng(start_node_input)
        end_coords = self._try_parse_lat_lng(end_node_input)
        if start_coords is None or end_coords is None:
            return self._parse_node_input(start_node_input), self._parse_node_input(end_node_input)

        start_candidates = self._nearest_node_candidates(start_coords[0], start_coords[1], limit=40)
        end_candidates = self._nearest_node_candidates(end_coords[0], end_coords[1], limit=40)
        if not start_candidates or not end_candidates:
            return self._parse_node_input(start_node_input), self._parse_node_input(end_node_input)

        end_by_component: dict[int, list[tuple[int, float]]] = {}
        for node_id, dist_m in end_candidates:
            comp_id = self.node_component.get(int(node_id), -1)
            end_by_component.setdefault(comp_id, []).append((int(node_id), float(dist_m)))
        for comp_list in end_by_component.values():
            comp_list.sort(key=lambda item: item[1])

        best_pair: tuple[int, int] | None = None
        best_score = float("inf")
        for start_node_id, start_dist_m in start_candidates:
            comp_id = self.node_component.get(int(start_node_id), -1)
            end_list = end_by_component.get(comp_id, [])
            if not end_list:
                continue
            end_node_id, end_dist_m = end_list[0]
            score = float(start_dist_m + end_dist_m)
            if score < best_score:
                best_score = score
                best_pair = (int(start_node_id), int(end_node_id))

        if best_pair is not None:
            return best_pair

        return int(start_candidates[0][0]), int(end_candidates[0][0])


    # HEURISTICS
    def euclidean_time_heuristic(
        self,
        current_node_id: int,  # Current node id.
        goal_node_id: int,  # Target node id.
        speed_mps: float = 15.0,  # Reference speed for converting straight-line distance to time.
    ) -> float:
        lat1 = float(self.graph.nodes[current_node_id]["lat"])
        lng1 = float(self.graph.nodes[current_node_id]["lng"])
        lat2 = float(self.graph.nodes[goal_node_id]["lat"])
        lng2 = float(self.graph.nodes[goal_node_id]["lng"])
        return math.hypot(lat2 - lat1, lng2 - lng1) * 111000.0 / max(speed_mps, 1e-6)

    def manhattan_time_heuristic(
        self,
        current_node_id: int,  # Current node id.
        goal_node_id: int,  # Target node id.
        speed_mps: float = 15.0,  # Reference speed for converting Manhattan distance to time.
    ) -> float:
        lat1 = float(self.graph.nodes[current_node_id]["lat"])
        lng1 = float(self.graph.nodes[current_node_id]["lng"])
        lat2 = float(self.graph.nodes[goal_node_id]["lat"])
        lng2 = float(self.graph.nodes[goal_node_id]["lng"])
        return (abs(lat2 - lat1) + abs(lng2 - lng1)) * 111000.0 / max(speed_mps, 1e-6)

    def _ml_penalty_heuristic(
        self,
        current_node_id: int,  # Current node id.
        goal_node_id: int,  # Target node id.
        model_key: str,  # Model identifier ("mlp" or "gnn") selecting node-penalty table.
    ) -> float:
        """
        ML-guided heuristic: Euclidean lower bound scaled by mean node penalty.
        This keeps ML algorithms focused on high-penalty regions earlier in search.
        """
        base = self.euclidean_time_heuristic(current_node_id, goal_node_id, speed_mps=15.0)
        penalty_map = self._node_penalty_mean.get(model_key, {})
        node_penalty = penalty_map.get(int(current_node_id), self.ml_penalty_min)
        node_penalty = clamp(self._safe_float(node_penalty, self.ml_penalty_min), self.ml_penalty_min, self.ml_penalty_max)
        return base * (1.0 + node_penalty)

    def mlp_time_heuristic(self, current_node_id: int, goal_node_id: int) -> float:
        """MLP-specific heuristic using node penalty means from MLP edge weights."""
        return self._ml_penalty_heuristic(current_node_id, goal_node_id, "mlp")

    def gnn_time_heuristic(self, current_node_id: int, goal_node_id: int) -> float:
        """GNN-specific heuristic using node penalty means from GNN edge weights."""
        return self._ml_penalty_heuristic(current_node_id, goal_node_id, "gnn")

    def alt_heuristic(
        self,
        current_node_id: int,  # Current node id.
        goal_node_id: int,  # Target node id.
        landmarks: list[int] | None = None,  # Optional landmark node set override.
        forward_distances: dict[int, dict[int, float]] | None = None,  # Optional precomputed distances landmark -> node.
        reverse_distances: dict[int, dict[int, float]] | None = None,  # Optional precomputed distances node -> landmark.
    ) -> float:
        if not self.alt_ready:
            self.prepare_alt_landmarks(weight_key="free_flow_time")

        active_landmarks = landmarks if landmarks is not None else self.landmarks
        active_forward = forward_distances if forward_distances is not None else self.alt_forward
        active_reverse = reverse_distances if reverse_distances is not None else self.alt_reverse
        if not active_landmarks:
            return self.euclidean_time_heuristic(current_node_id, goal_node_id)

        lower_bound = 0.0
        for landmark in active_landmarks:
            forward_map = active_forward.get(landmark, {})
            reverse_map = active_reverse.get(landmark, {})

            d_landmark_to_goal = forward_map.get(goal_node_id, 0.0)
            d_landmark_to_node = forward_map.get(current_node_id, 0.0)
            d_node_to_landmark = reverse_map.get(current_node_id, 0.0)
            d_goal_to_landmark = reverse_map.get(goal_node_id, 0.0)

            candidate = max(
                d_landmark_to_goal - d_landmark_to_node,
                d_node_to_landmark - d_goal_to_landmark,
                0.0,
            )
            if candidate > lower_bound:
                lower_bound = candidate

        return lower_bound

    def _weight_key(self, algorithm_key: str) -> str:
        return {
            "astar": "free_flow_time",
            "astar_manhattan": "free_flow_time",
            "alt": "free_flow_time",
            "mlp": "mlp_time",
            "gnn": "gnn_time",
        }[algorithm_key]


    # SHORTEST-PATH SEARCH
    def a_star_with_metrics(
        self,
        start_node_id: int,  # Start node id.
        goal_node_id: int,  # Goal node id.
        weight_key: str,  # Edge attribute used as traversal cost (seconds).
        heuristic_fn: Callable[[int, int], float],  # Heuristic function h(current_node_id, goal_node_id) in seconds.
    ) -> dict[str, Any]:
  
        started = time.perf_counter()
        open_heap: list[tuple[float, float, int]] = []
        heapq.heappush(open_heap, (heuristic_fn(start_node_id, goal_node_id), 0.0, start_node_id))

        came_from: dict[int, int] = {}
        g_score: dict[int, float] = {start_node_id: 0.0}
        closed_set: set[int] = set()
        expanded_nodes = 0

        while open_heap:
            _, current_cost, current_node_id = heapq.heappop(open_heap)
            if current_node_id in closed_set:
                continue

            closed_set.add(current_node_id)
            expanded_nodes += 1

            if current_node_id == goal_node_id:
                break

            for neighbor_node_id, edge_data in self.graph[current_node_id].items():
                if weight_key not in edge_data:
                    continue
                tentative_cost = current_cost + float(edge_data[weight_key])
                neighbor_node_id_int = int(neighbor_node_id)
                if tentative_cost < g_score.get(neighbor_node_id_int, float("inf")):
                    came_from[neighbor_node_id_int] = int(current_node_id)
                    g_score[neighbor_node_id_int] = tentative_cost
                    heuristic_cost = heuristic_fn(neighbor_node_id_int, goal_node_id)
                    heapq.heappush(open_heap, (tentative_cost + heuristic_cost, tentative_cost, neighbor_node_id_int))

        if goal_node_id not in came_from and goal_node_id != start_node_id:
            raise ValueError("No route found between selected points.")

        path = [goal_node_id]
        cursor = goal_node_id
        while cursor != start_node_id:
            cursor = came_from[cursor]
            path.append(cursor)
        path.reverse()

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        dist_m = 0.0
        t_s = 0.0
        for u, v in zip(path[:-1], path[1:]):
            dist_m += float(self.graph[u][v]["length_m"])
            t_s += float(self.graph[u][v][weight_key])

        return {
            "path_nodes": path,
            "distance_km": dist_m / 1000.0,
            "time_min": t_s / 60.0,
            "expanded_nodes": expanded_nodes,
            "execution_ms": elapsed_ms,
        }

    # PUBLIC ROUTING API
    def route(
        self,
        start_node_input: str,  # User input for start point: "lat,lng" or numeric node id.
        end_node_input: str,  # User input for end point: "lat,lng" or numeric node id.
        algorithm_key: str,  # Canonical algorithm key: astar|astar_manhattan|alt|mlp|gnn.
    ) -> dict[str, Any]:
        self._ensure_graph_for_route_points(start_node_input, end_node_input)
        self._ensure_algorithm_available(algorithm_key)
        start_node_id, goal_node_id = self._resolve_route_node_pair(start_node_input, end_node_input)
        weight_key = self._weight_key(algorithm_key)

        heuristic: Callable[[int, int], float]
        if algorithm_key == "alt":
            heuristic = self.alt_heuristic
        elif algorithm_key == "astar_manhattan":
            heuristic = self.manhattan_time_heuristic
        elif algorithm_key == "mlp":
            heuristic = self.mlp_time_heuristic
        elif algorithm_key == "gnn":
            heuristic = self.gnn_time_heuristic
        else:
            heuristic = self.euclidean_time_heuristic

        search_result = self.a_star_with_metrics(start_node_id, goal_node_id, weight_key, heuristic)
        path = list(search_result["path_nodes"])
        coords = [[self.graph.nodes[node]["lat"], self.graph.nodes[node]["lng"]] for node in path]

        return {
            "algorithm": algorithm_key,
            "path": coords,
            "distance_km": round(float(search_result["distance_km"]), 3),
            "time_min": round(float(search_result["time_min"]), 3),
            "execution_ms": round(float(search_result["execution_ms"]), 3),
            "expanded_nodes": int(search_result["expanded_nodes"]),
            "visited_nodes": len(path),
            "start_resolved": {
                "node_id": start_node_id,
                "lat": self.graph.nodes[start_node_id]["lat"],
                "lng": self.graph.nodes[start_node_id]["lng"],
            },
            "end_resolved": {
                "node_id": goal_node_id,
                "lat": self.graph.nodes[goal_node_id]["lat"],
                "lng": self.graph.nodes[goal_node_id]["lng"],
            },
        }




# FASTAPI APP, AUTH, CONTENT, AND ROUTE ENDPOINTS

app = FastAPI(title="AI Navigation API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

router_service = RoutingService()
comments_store: list[dict[str, Any]] = load_json(COMMENTS_FILE, [])
history_store: list[dict[str, Any]] = load_json(HISTORY_FILE, [])
users_store: list[dict[str, Any]] = load_json(USERS_FILE, [])
articles_store: list[dict[str, Any]] = load_json(
    ARTICLES_FILE,
    [
        {
            "id": 1,
            "title": "A* and ALT heuristics",
            "body": "A* uses admissible heuristics. ALT tightens lower bounds via landmarks and triangle inequality.",
            "image_url": None,
            "created_at": now_utc().isoformat(),
            "author": "System",
        },
        {
            "id": 2,
            "title": "MLP and GNN travel-time penalties",
            "body": "MLP captures local traffic effects while GNN models broader topology-aware congestion.",
            "image_url": None,
            "created_at": now_utc().isoformat(),
            "author": "System",
        },
    ],
)
sessions_store: dict[str, int] = {}

def normalize_users_store_records() -> bool:
    changed = False
    for user in users_store:
        raw_email = str(user.get("email", "")).strip()
        raw_name = str(user.get("name", "")).strip()

        if raw_name and "@" in raw_name and raw_email and "@" not in raw_email:
            repaired_name = raw_email if len(raw_email) >= 2 else raw_name.split("@", 1)[0]
            user["email"] = normalize_email(raw_name)
            user["name"] = repaired_name
            raw_email = str(user["email"])
            raw_name = str(user["name"])
            changed = True

        normalized_email = normalize_email(raw_email)
        if user.get("email") != normalized_email:
            user["email"] = normalized_email
            changed = True

        if user.get("name") != raw_name:
            user["name"] = raw_name
            changed = True

    return changed

if not ARTICLES_FILE.exists():
    save_json(ARTICLES_FILE, articles_store)
if not COMMENTS_FILE.exists():
    save_json(COMMENTS_FILE, comments_store)
if not USERS_FILE.exists():
    save_json(USERS_FILE, users_store)
elif normalize_users_store_records():
    save_json(USERS_FILE, users_store)
if not HISTORY_FILE.exists():
    save_json(HISTORY_FILE, history_store)



# STORE HELPERS AND ACCESS CONTROL

def find_user_by_id(user_id: int) -> dict[str, Any] | None:
    wanted_id = int(user_id)
    for item in users_store:
        if int(item.get("id", -1)) == wanted_id:
            return item
    return None


def find_user_by_email(email: str) -> dict[str, Any] | None:
    for item in users_store:
        if item.get("email") == email:
            return item
    return None


def find_article_by_id(article_id: int) -> dict[str, Any] | None:
    wanted_id = int(article_id)
    for item in articles_store:
        if int(item.get("id", -1)) == wanted_id:
            return item
    return None


def article_comments(article_id: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    wanted_article_id = int(article_id)
    for item in comments_store:
        if int(item.get("article_id", -1)) == wanted_article_id:
            rows.append(item)
    return rows


def normalize_person_name(value: str | None) -> str:
    return str(value or "").strip().lower()


def can_delete_comment_item(
    comment: dict[str, Any],
    current_user: dict[str, Any] | None,
    guest_author_name: str | None,
) -> bool:
    if current_user and current_user.get("is_admin", False):
        return True

    comment_user_id = comment.get("author_user_id")
    if current_user and comment_user_id is not None:
        try:
            return int(comment_user_id) == int(current_user.get("id"))
        except (TypeError, ValueError):
            return False

    if current_user:
        return normalize_person_name(comment.get("name")) in {
            normalize_person_name(current_user.get("name")),
            normalize_person_name(current_user.get("email")),
        }

    if comment_user_id is not None:
        return False

    return normalize_person_name(comment.get("name")) == normalize_person_name(guest_author_name)


def resolve_user_from_auth(authorization: str | None) -> dict[str, Any] | None:
    token = parse_bearer_token(authorization)
    
    if not token:
        return None
    user_id = sessions_store.get(token)
    if user_id is None:
        return None
    return find_user_by_id(user_id)


def require_user(authorization: str | None) -> dict[str, Any]:
    user = resolve_user_from_auth(authorization)
    
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_admin(authorization: str | None) -> dict[str, Any]:
    user = require_user(authorization)
    
    if not user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user




# HEALTH AND DIAGNOSTICS ENDPOINTS

@app.get("/api/health")
def health() -> dict[str, Any]:
    model_state = router_service.model_status()
    return {
        "status": "ok",
        "graph_source": router_service.graph_source,
        "graph_bbox": router_service.graph_bbox,
        "active_cache_file": str(router_service.active_cache_file) if router_service.active_cache_file else None,
        "auto_fetch_osm_graph": router_service.auto_fetch_osm,
        "graph_nodes": router_service.graph.number_of_nodes(),
        "graph_edges": router_service.graph.number_of_edges(),
        "models": model_state,
        "available_algorithms": router_service.available_algorithms(),
    }




# AUTHENTICATION ENDPOINTS

@app.post("/api/auth/register")
def register(payload: RegisterRequest) -> dict[str, Any]:
    if find_user_by_email(payload.email):
        raise HTTPException(status_code=409, detail="Email is already registered")

    user_id = int(now_utc().timestamp() * 1000)
    salt = secrets.token_hex(8)
    user = {
        "id": user_id,
        "email": payload.email,
        "name": payload.name,
        "salt": salt,
        "password_hash": hash_password(payload.password, salt),
        "is_admin": len(users_store) == 0,
        "created_at": now_utc().isoformat(),
    }
    users_store.append(user)
    save_json(USERS_FILE, users_store)

    token = secrets.token_urlsafe(32)
    sessions_store[token] = user_id

    return {"token": token, "user": public_user(user)}


@app.post("/api/auth/login")
def login(payload: LoginRequest) -> dict[str, Any]:
    user = find_user_by_email(payload.email)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    expected_hash = hash_password(payload.password, str(user["salt"]))
    if expected_hash != user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = secrets.token_urlsafe(32)
    sessions_store[token] = int(user["id"])
    return {"token": token, "user": public_user(user)}


@app.get("/api/auth/me")
def me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    user = require_user(authorization)
    return {"user": public_user(user)}


@app.post("/api/auth/logout")
def logout(authorization: str | None = Header(default=None)) -> dict[str, str]:
    token = parse_bearer_token(authorization)
    if token and token in sessions_store:
        sessions_store.pop(token, None)
    return {"status": "ok"}




# ROUTING ENDPOINTS

@app.post("/api/route")
def compute_route(
    payload: RouteRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        result = router_service.route(payload.start_node, payload.end_node, payload.algorithm)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    current_user = resolve_user_from_auth(authorization)
    history_store.insert(
        0,
        {
            "id": int(now_utc().timestamp() * 1000),
            "date": now_utc().strftime("%Y-%m-%d"),
            "start": payload.start_node,
            "end": payload.end_node,
            "algorithm": result["algorithm"],
            "status": "Completed",
            "distance_km": result["distance_km"],
            "time_min": result["time_min"],
            "execution_ms": result["execution_ms"],
            "user_id": current_user.get("id") if current_user else None,
            "user_email": current_user.get("email") if current_user else None,
        },
    )
    del history_store[500:]
    save_json(HISTORY_FILE, history_store)
    return result



# ARTICLES AND ARTICLE COMMENTS ENDPOINTS

@app.get("/api/articles")
def get_articles() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for article in articles_store:
        item = dict(article)
        item["comment_count"] = len(article_comments(int(article.get("id", -1))))
        rows.append(item)
    rows.sort(key=lambda item: item.get("id", 0), reverse=True)
    return rows


@app.post("/api/articles")
def create_article(
    payload: ArticleRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    admin = require_admin(authorization)
    article = {
        "id": int(now_utc().timestamp() * 1000),
        "title": payload.title,
        "body": payload.body,
        "image_url": payload.image_url,
        "created_at": now_utc().isoformat(),
        "author": admin.get("name") or admin.get("email"),
    }
    articles_store.insert(0, article)
    save_json(ARTICLES_FILE, articles_store)
    return article


@app.delete("/api/articles/{article_id}")
def delete_article(
    article_id: int,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin(authorization)

    article_idx = -1
    for idx, item in enumerate(articles_store):
        if int(item.get("id", -1)) == int(article_id):
            article_idx = idx
            break
    if article_idx < 0:
        raise HTTPException(status_code=404, detail="Article not found")

    removed_article = articles_store.pop(article_idx)
    save_json(ARTICLES_FILE, articles_store)

    deleted_comments = 0
    kept_comments: list[dict[str, Any]] = []
    for item in comments_store:
        if int(item.get("article_id", -1)) == int(article_id):
            deleted_comments += 1
        else:
            kept_comments.append(item)
    comments_store[:] = kept_comments
    if deleted_comments:
        save_json(COMMENTS_FILE, comments_store)

    return {
        "status": "ok",
        "deleted_article_id": removed_article.get("id"),
        "deleted_comments": deleted_comments,
    }


@app.get("/api/articles/{article_id}/comments")
def get_article_comments(
    article_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    author_name: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Return paginated comments for one article with delete permissions."""
    if find_article_by_id(article_id) is None:
        raise HTTPException(status_code=404, detail="Article not found")

    current_user = resolve_user_from_auth(authorization)
    rows = article_comments(article_id)
    paged = rows[offset : offset + limit]
    payload_items: list[dict[str, Any]] = []
    for item in paged:
        row = dict(item)
        row["can_delete"] = can_delete_comment_item(item, current_user, author_name)
        payload_items.append(row)

    return {
        "items": payload_items,
        "offset": offset,
        "limit": limit,
        "total": len(rows),
        "has_more": offset + limit < len(rows),
    }


@app.post("/api/articles/{article_id}/comments")
def post_article_comment(
    article_id: int,
    payload: ArticleCommentRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if find_article_by_id(article_id) is None:
        raise HTTPException(status_code=404, detail="Article not found")

    current_user = resolve_user_from_auth(authorization)
    author_name = payload.name
    if current_user:
        author_name = current_user.get("name") or current_user.get("email")
    if not author_name:
        raise HTTPException(status_code=400, detail="Author name is required for anonymous comment")

    item = {
        "id": int(now_utc().timestamp() * 1000),
        "article_id": int(article_id),
        "name": str(author_name).strip(),
        "text": payload.text,
        "author_user_id": current_user.get("id") if current_user else None,
        "author_email": current_user.get("email") if current_user else None,
        "created_at": now_utc().isoformat(),
    }
    comments_store.insert(0, item)
    del comments_store[2000:]
    save_json(COMMENTS_FILE, comments_store)
    return item


@app.delete("/api/articles/{article_id}/comments/{comment_id}")
def delete_article_comment(
    article_id: int,
    comment_id: int,
    author_name: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    current_user = resolve_user_from_auth(authorization)

    comment_idx = next(
        (
            idx
            for idx, item in enumerate(comments_store)
            if int(item.get("id", -1)) == int(comment_id)
            and int(item.get("article_id", -1)) == int(article_id)
        ),
        None,
    )
    if comment_idx is None:
        raise HTTPException(status_code=404, detail="Comment not found")

    comment_item = comments_store[comment_idx]
    if not can_delete_comment_item(comment_item, current_user, author_name):
        raise HTTPException(status_code=403, detail="Only author or admin can delete this comment")

    removed = comments_store.pop(comment_idx)
    save_json(COMMENTS_FILE, comments_store)
    return {"status": "ok", "deleted_id": removed.get("id")}



# GENERIC COMMENTS ENDPOINTS

@app.get("/api/comments")
def get_comments(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    rows = comments_store[offset : offset + limit]
    return {
        "items": rows,
        "offset": offset,
        "limit": limit,
        "total": len(comments_store),
        "has_more": offset + limit < len(comments_store),
    }


@app.post("/api/comments")
def post_comment(payload: CommentRequest) -> dict[str, Any]:
    """Create one generic comment entry."""
    item = {
        "id": int(now_utc().timestamp() * 1000),
        "name": payload.name,
        "text": payload.text,
        "created_at": now_utc().isoformat(),
    }
    comments_store.insert(0, item)
    del comments_store[1000:]
    save_json(COMMENTS_FILE, comments_store)
    return item



# HISTORY ENDPOINTS

@app.get("/api/history")
def get_history(
    limit: int = Query(100, ge=1, le=500),
    authorization: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    """Return route history (admin sees all; user sees own records)."""
    user = resolve_user_from_auth(authorization)
    if user and not user.get("is_admin", False):
        rows = [item for item in history_store if item.get("user_id") == user.get("id")]
        return rows[:limit]
    return history_store[:limit]


@app.get("/api/history/me")
def get_my_history(
    limit: int = Query(100, ge=1, le=500),
    authorization: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    """Return current user's own route history."""
    user = require_user(authorization)
    rows = [item for item in history_store if item.get("user_id") == user.get("id")]
    return rows[:limit]
