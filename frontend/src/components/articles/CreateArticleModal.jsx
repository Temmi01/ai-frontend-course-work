import { useState } from "react";

export default function CreateArticleModal({ isOpen, onClose, onSubmit, busy = false }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [imageUrl, setImageUrl] = useState("");
  const [error, setError] = useState("");

  if (!isOpen) {
    return null;
  }

  function resetAndClose() {
    setTitle("");
    setBody("");
    setImageUrl("");
    setError("");
    onClose();
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    try {
      await onSubmit({
        title,
        body,
        image_url: imageUrl.trim() || null,
      });
      resetAndClose();
    } catch (err) {
      setError(err?.message || "Не вдалося створити статтю");
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="Створення статті">
      <div className="modal-card">
        <button type="button" className="modal-close" onClick={resetAndClose} aria-label="Закрити">
          ×
        </button>
        <h3>Додати статтю</h3>
        <form className="comment-form" onSubmit={handleSubmit}>
          <label>
            Заголовок
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Введіть заголовок"
              required
              minLength={4}
              maxLength={240}
            />
          </label>

          <label>
            Текст
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="Опишіть матеріал статті"
              required
              minLength={20}
              maxLength={12000}
            />
          </label>

          <label>
            Зображення (URL)
            <input
              type="text"
              value={imageUrl}
              onChange={(e) => setImageUrl(e.target.value)}
              placeholder="https://..."
            />
          </label>

          {error ? <div className="field-error">{error}</div> : null}
          <div className="actions-row">
            <button className="primary-btn" type="submit" disabled={busy}>
              {busy ? "Збереження..." : "Опублікувати"}
            </button>
            <button className="secondary-btn" type="button" onClick={resetAndClose} disabled={busy}>
              Скасувати
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
