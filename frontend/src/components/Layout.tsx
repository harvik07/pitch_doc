import { useEffect, useRef } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { api } from "../api";

/** The brand bar (brand only, never navigation), the page, and a quiet footer. Focus moves to the page on each
 * route change so screen-reader users land on the new content. */
export default function Layout() {
  const { pathname } = useLocation();
  const main = useRef<HTMLElement>(null);
  const first = useRef(true);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    window.scrollTo({ top: 0 });
    main.current?.focus({ preventScroll: true });
  }, [pathname]);

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="brandbar">
        <div className="container brandbar__inner">
          <Link to="/" className="brand" aria-label="TRACE by Marsh, home">
            <img className="brand__logo" src={api.logoUrl} alt="Marsh" width={90} height={30} />
            <span className="brand__rule" aria-hidden="true" />
            <span className="brand__product">Trace</span>
          </Link>
        </div>
      </header>
      <main id="main" ref={main} tabIndex={-1}>
        <Outlet />
      </main>
      <footer className="footer">
        <div className="container footer__inner">
          <span>Confidential · For Marsh advisors</span>
          <span>Every statement traced to its source</span>
        </div>
      </footer>
    </div>
  );
}
