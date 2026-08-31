import { Link, Route, Routes } from "react-router-dom";

import { Home } from "./pages/Home";
import { Login } from "./pages/Login";
import { Signup } from "./pages/Signup";

export function App() {
  return (
    <div>
      <nav>
        <Link to="/">Reconcilio</Link>
        <Link to="/login">Log in</Link>
        <Link to="/signup">Sign up</Link>
      </nav>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />
      </Routes>
    </div>
  );
}
