import { useMemo, useState } from "react";

export default function ArticleModal({
  article,
  currentUser,
  comments = [],
  loadingComments = false,
  hasMore = false,
  onLoadMore,
  onSubmitComment,
  onDeleteComment,
  onClose,
}) {
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const authorLabel = useMemo(() => {
    if (currentUser) {
      return currentUser.name || currentUser.email || "";
    }
    return name;
  }, [currentUser, name]);

  if (!article) {
    return null;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    if (!text.trim()) {
      setError("Введіть текст коментаря");
      return;
    }
    if (!currentUser && !name.trim()) {
      setError("Вкажіть ім'я");
      return;
    }
    setBusy(true);
    try {
      await onSubmitComment({
        text: text.trim(),
        name: currentUser ? (currentUser.name || currentUser.email || "").trim() : name.trim(),
      });
      setText("");
    } catch (err) {
      setError(err?.message || "Не вдалося додати коментар");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(comment) {
    try {
      await onDeleteComment(comment, authorLabel);
    } catch (err) {
      setError(err?.message || "Не вдалося видалити коментар");
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="Перегляд статті">
      <div className="modal-card">
        <button type="button" className="modal-close" onClick={onClose} aria-label="Закрити">
          ×
        </button>
        <h3>{article.title}</h3>
        <div className="muted">
          {article.author || "Невідомий автор"} · {new Date(article.created_at).toLocaleString("uk-UA")}
        </div>
        {article.image_url ? (
          <img className="article-modal-image" src={article.image_url} alt={`Зображення статті ${article.title}`} />
        ) : null}
        <div className="article-modal-body">{article.body}</div>

        <section className="modal-comments">
          <h4>Коментарі</h4>
          <form className="comment-form" onSubmit={handleSubmit}>
            {!currentUser ? (
              <label>
                Ваше ім'я
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Вкажіть ім'я"
                  maxLength={120}
                />
              </label>
            ) : null}
            <label>
              Коментар
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Напишіть коментар"
                maxLength={3000}
              />
            </label>
            {error ? <div className="field-error">{error}</div> : null}
            <div className="actions-row">
              <button className="primary-btn" type="submit" disabled={busy}>
                {busy ? "Надсилання..." : "Додати коментар"}
              </button>
            </div>
          </form>

          {loadingComments ? (
            <div className="inline-loader">
              <span className="spinner" />
              Завантаження коментарів...
            </div>
          ) : (
            <ul className="comment-list">
              {comments.map((comment) => (
                <li key={comment.id} className="comment-item">
                  <div className="comment-header">
                    <strong>{comment.name || "Користувач"}</strong>
                    <div className="actions-row">
                      <small className="muted">{new Date(comment.created_at).toLocaleString("uk-UA")}</small>
                      {comment.can_delete ? (
                        <button
                          type="button"
                          className="danger-btn"
                          onClick={() => handleDelete(comment)}
                        >
                          Видалити
                        </button>
                      ) : null}
                    </div>
                  </div>
                  <p>{comment.text}</p>
                </li>
              ))}
            </ul>
          )}

          {hasMore ? (
            <div className="actions-row">
              <button type="button" className="secondary-btn" onClick={onLoadMore} disabled={loadingComments}>
                Завантажити ще
              </button>
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
