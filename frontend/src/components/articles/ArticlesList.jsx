export default function ArticlesList({ articles, currentUser, onOpenArticle, onDeleteArticle }) {
  const isAdmin = Boolean(currentUser?.is_admin);

  async function handleDelete(article) {
    if (!isAdmin || !onDeleteArticle) {
      return;
    }
    const confirmed = window.confirm(`Видалити статтю "${article.title}"?`);
    if (!confirmed) {
      return;
    }
    try {
      await onDeleteArticle(article);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Не вдалося видалити статтю";
      window.alert(message);
    }
  }

  return (
    <ul className="articles-list">
      {articles.map((article) => (
        <li key={article.id} className="article-list-item">
          <div className="article-row">
            <button type="button" className="article-open-btn" onClick={() => onOpenArticle(article)}>
              <span className="article-title">{article.title}</span>
              <span className="article-meta">
                {article.author || "Невідомий автор"} · {new Date(article.created_at).toLocaleString("uk-UA")}
              </span>
              <span className="article-snippet">{String(article.body || "").slice(0, 180)}...</span>
            </button>
            {isAdmin ? (
              <button type="button" className="danger-btn article-delete-btn" onClick={() => handleDelete(article)}>
                Видалити
              </button>
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  );
}
