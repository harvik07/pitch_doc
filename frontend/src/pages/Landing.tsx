import { Link } from "react-router-dom";
import { ArrowRight } from "@phosphor-icons/react";
import { usePageTitle } from "../components/usePageTitle";

export default function Landing() {
  usePageTitle("");
  return (
    <section className="landing" aria-labelledby="landing-title">
      <div className="container landing__grid">
        <h1 id="landing-title" className="landing__mark">
          TRACE
        </h1>
        <div className="landing__rule" aria-hidden="true" />
        <div>
          <p className="landing__product">Marsh Pitch Intelligence</p>
          <p className="landing__tagline">Evidence-led client pitch generation</p>
        </div>
        <div className="landing__cta">
          <Link to="/new" className="btn btn--lg">
            Get Started
            <ArrowRight className="arrow" size={20} weight="bold" aria-hidden="true" />
          </Link>
        </div>
      </div>
    </section>
  );
}
