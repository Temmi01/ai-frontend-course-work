import L from "leaflet";
import { MapContainer, Marker, Polyline, TileLayer, useMapEvents, ZoomControl } from "react-leaflet";
import iconRetina from "leaflet/dist/images/marker-icon-2x.png";
import iconUrl from "leaflet/dist/images/marker-icon.png";
import shadowUrl from "leaflet/dist/images/marker-shadow.png";

L.Icon.Default.mergeOptions({
  iconRetinaUrl: iconRetina,
  iconUrl,
  shadowUrl,
});

const markerIcon = new L.Icon({
  iconRetinaUrl: iconRetina,
  iconUrl,
  shadowUrl,
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
});

const KYIV_CENTER = [50.4501, 30.5234];

function ClickPicker({ pickMode, onPick }) {
  useMapEvents({
    click(event) {
      if (!pickMode) {
        return;
      }

      const lat = event.latlng.lat;
      const lng = event.latlng.lng;
      onPick(pickMode, `${lat.toFixed(6)}, ${lng.toFixed(6)}`, [lat, lng]);
    },
  });

  return null;
}

export default function LeafletRouteMap({
  routeLayers,
  routeColors,
  pickMode,
  onPick,
  startMarker,
  endMarker,
}) {
  return (
    <MapContainer center={KYIV_CENTER} zoom={12} className="map-canvas" zoomControl={false}>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <ZoomControl position="bottomleft" />
      <ClickPicker pickMode={pickMode} onPick={onPick} />

      {(routeLayers || []).map((row) => (
        <Polyline
          key={row.algorithm}
          positions={row.path}
          pathOptions={{
            color: routeColors?.[row.algorithm] || "#0b7285",
            weight: 5,
            opacity: 0.85,
          }}
        />
      ))}

      {startMarker ? <Marker position={startMarker} icon={markerIcon} /> : null}
      {endMarker ? <Marker position={endMarker} icon={markerIcon} /> : null}
    </MapContainer>
  );
}
