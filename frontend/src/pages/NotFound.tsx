import { Link } from "react-router-dom";
import { usePageTitle } from "../components/usePageTitle";

export default function NotFound() {
  usePageTitle("Page not found");
  return (
    <div className="page container">
      <div className="center-state">
        <p className="eyebrow">Not found</p>
        <h1 className="display">This page doesn't exist</h1>
        <p className="lead">The link may be out of date.</p>
        <Link to="/" className="btn">
          Go to TRACE
        </Link>
      </div>
    </div>
  );
}
