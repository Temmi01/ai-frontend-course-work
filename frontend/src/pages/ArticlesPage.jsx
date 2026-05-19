import { useEffect, useState } from "react";
import {
  createArticle,
  deleteArticle,
  deleteArticleComment,
  getArticleComments,
  getArticles,
  getMe,
  postArticleComment,
} from "../core/api";
import { clearSession, getUser } from "../core/session";
import ArticlesList from "../components/articles/ArticlesList";
import ArticleModal from "../components/articles/ArticleModal";
import CreateArticleModal from "../components/articles/CreateArticleModal";

export default function ArticlesPage() {
  const [articles, setArticles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [currentUser, setCurrentUser] = useState(getUser());

  const [openedArticle, setOpenedArticle] = useState(null);
  const [comments, setComments] = useState([]);
  const [commentsOffset, setCommentsOffset] = useState(0);
  const [commentsHasMore, setCommentsHasMore] = useState(false);
  const [loadingComments, setLoadingComments] = useState(false);

  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [creatingArticle, setCreatingArticle] = useState(false);

  async function loadArticles() {
    setLoading(true);
    setError("");
    try {
      const rows = await getArticles();
      setArticles(rows);
    } catch (err) {
      setError(err?.message || "Не вдалося завантажити статті");
    } finally {
      setLoading(false);
    }
  }

  async function loadMeSafe() {
    try {
      const me = await getMe();
      setCurrentUser(me?.user || null);
    } catch (err) {
      const message = String(err?.message || "").toLowerCase();
      const authFailed = message.includes("401") || message.includes("потрібна авторизація");
      if (authFailed) {
        clearSession();
        setCurrentUser(null);
        return;
      }
      setCurrentUser(getUser());
    }
  }

  useEffect(() => {
    loadArticles();
    loadMeSafe();
  }, []);

  async function loadComments(articleId, offset = 0, append = false) {
    setLoadingComments(true);
    try {
      const payload = await getArticleComments(articleId, {
        offset,
        limit: 20,
      });
      setComments((prev) => (append ? prev.concat(payload.items || []) : payload.items || []));
      setCommentsOffset(offset + (payload.items || []).length);
      setCommentsHasMore(Boolean(payload.has_more));
    } finally {
      setLoadingComments(false);
    }
  }

  async function handleOpenArticle(article) {
    setOpenedArticle(article);
    await loadComments(article.id, 0, false);
  }

  async function handleLoadMore() {
    if (!openedArticle) {
      return;
    }
    await loadComments(openedArticle.id, commentsOffset, true);
  }

  async function handleSubmitComment(payload) {
    if (!openedArticle) {
      return;
    }
    await postArticleComment(openedArticle.id, payload);
    await loadComments(openedArticle.id, 0, false);
    await loadArticles();
  }

  async function handleDeleteComment(comment, authorName) {
    if (!openedArticle) {
      return;
    }
    await deleteArticleComment(openedArticle.id, comment.id, authorName);
    await loadComments(openedArticle.id, 0, false);
    await loadArticles();
  }

  async function handleCreateArticle(payload) {
    setCreatingArticle(true);
    try {
      await createArticle(payload);
      await loadArticles();
    } finally {
      setCreatingArticle(false);
    }
  }

  async function handleDeleteArticle(article) {
    await deleteArticle(article.id);
    if (openedArticle && openedArticle.id === article.id) {
      setOpenedArticle(null);
      setComments([]);
      setCommentsOffset(0);
      setCommentsHasMore(false);
    }
    await loadArticles();
  }

  return (
    <section className="card articles-card">
      <div className="articles-toolbar">
        <h2>Статті</h2>
        {currentUser?.is_admin ? (
          <button className="primary-btn" type="button" onClick={() => setIsCreateOpen(true)}>
            Додати статтю
          </button>
        ) : null}
      </div>

      {loading ? <p className="muted">Завантаження...</p> : null}
      {error ? <p className="field-error">{error}</p> : null}
      {!loading && !error ? (
        <ArticlesList
          articles={articles}
          currentUser={currentUser}
          onOpenArticle={handleOpenArticle}
          onDeleteArticle={handleDeleteArticle}
        />
      ) : null}

      <CreateArticleModal
        isOpen={isCreateOpen}
        onClose={() => setIsCreateOpen(false)}
        onSubmit={handleCreateArticle}
        busy={creatingArticle}
      />

      <ArticleModal
        article={openedArticle}
        currentUser={currentUser}
        comments={comments}
        loadingComments={loadingComments}
        hasMore={commentsHasMore}
        onLoadMore={handleLoadMore}
        onSubmitComment={handleSubmitComment}
        onDeleteComment={handleDeleteComment}
        onClose={() => setOpenedArticle(null)}
      />
    </section>
  );
}
