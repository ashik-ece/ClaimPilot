import { Link, Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import ClaimPage from "./pages/ClaimPage";
import Login from "./pages/Login";
import Queue from "./pages/Queue";

export default function App() {
  const { signedIn, logout } = useAuth();
  if (!signedIn) return <Login />;
  return (
    <>
      <header className="topbar">
        <Link to="/" className="brand">
          ClaimPilot
        </Link>
        <span className="muted">The AI proposes. You decide.</span>
        <button className="link" onClick={logout}>
          Sign out
        </button>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Queue />} />
          <Route path="/claims/:id" element={<ClaimPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}
