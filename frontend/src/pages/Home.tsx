import { Navigate, useNavigate } from "react-router-dom";

import { clearSession, getSession } from "../session";

export function Home() {
  const navigate = useNavigate();
  const session = getSession();

  if (!session) {
    return <Navigate to="/login" replace />;
  }

  function handleLogout() {
    clearSession();
    navigate("/login");
  }

  return (
    <div>
      <h1>{session.organization.name}</h1>
      <p>Signed in as {session.user.email} ({session.role})</p>
      <button type="button" onClick={handleLogout}>
        Log out
      </button>
    </div>
  );
}
