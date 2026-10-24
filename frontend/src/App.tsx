import { Link, Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import ClaimPage from "./pages/ClaimPage";
import Feedback from "./pages/Feedback";
import Login from "./pages/Login";
import Queue from "./pages/Queue";
import Tasks from "./pages/Tasks";

export default function App() {
  const { signedIn, logout } = useAuth();
  if (!signedIn) return <Login />;
  return (
    <>
      <header className="topbar">
        <Link to="/" className="brand">
          ClaimPilot
        </Link>
        <Link to="/tasks">My tasks</Link>
        <Link to="/feedback">Feedback</Link>
        <span className="muted">The AI proposes. You decide.</span>
        <button className="link" onClick={logout}>
          Sign out
        </button>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Queue />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/feedback" element={<Feedback />} />
          <Route path="/claims/:id" element={<ClaimPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}
