import { useEffect, useId, useRef, useState, type DragEvent, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ArrowRight, FilePdf, Info, UploadSimple, WarningCircle, X } from "@phosphor-icons/react";
import { api, ApiError, type Policy } from "../api";
import { usePageTitle } from "../components/usePageTitle";

interface Errors {
  company_name?: string[];
  documents?: string[];
}

const MAX_NAME = 120;

export default function CreatePitch() {
  usePageTitle("Create a pitch");
  const navigate = useNavigate();
  const location = useLocation();
  const initialName = (location.state as { companyName?: string } | null)?.companyName ?? "";

  const [policies, setPolicies] = useState<Policy[] | null>(null);
  const [maxMb, setMaxMb] = useState(25);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [company, setCompany] = useState(initialName);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [errors, setErrors] = useState<Errors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const summaryRef = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const nameId = useId();
  const nameHint = useId();
  const nameErr = useId();
  const docsErr = useId();

  useEffect(() => {
    api
      .policies()
      .then((data) => {
        setPolicies(data.policies);
        setMaxMb(data.max_file_mb);
        setSelected(new Set(data.policies.map((p) => p.id)));
      })
      .catch((e: ApiError) => setLoadError(e.message));
  }, []);

  const hasErrors = Boolean(errors.company_name?.length || errors.documents?.length || formError);

  useEffect(() => {
    if (hasErrors) summaryRef.current?.focus();
  }, [hasErrors, errors, formError]);

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setErrors((e) => ({ ...e, documents: undefined }));
  }

  function addFiles(list: FileList | null) {
    if (!list) return;
    const incoming = Array.from(list);
    setFiles((current) => {
      const names = new Set(current.map((f) => `${f.name}:${f.size}`));
      return [...current, ...incoming.filter((f) => !names.has(`${f.name}:${f.size}`))];
    });
    setErrors((e) => ({ ...e, documents: undefined }));
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    addFiles(event.dataTransfer.files);
  }

  function validate(): Errors {
    const found: Errors = {};
    const name = company.trim();
    if (!name) found.company_name = ["Please enter a company name."];
    else if (name.length < 2) found.company_name = ["The company name must be at least 2 characters."];
    else if (name.length > MAX_NAME) found.company_name = [`The company name must be at most ${MAX_NAME} characters.`];
    const docs: string[] = [];
    if (selected.size === 0 && files.length === 0) {
      docs.push("Please select or upload at least one policy document (PDF).");
    }
    for (const f of files) {
      if (!f.name.toLowerCase().endsWith(".pdf")) docs.push(`'${f.name}' isn't a PDF. Please add policy documents as PDF files.`);
      else if (f.size === 0) docs.push(`'${f.name}' is empty (0 bytes).`);
      else if (f.size > maxMb * 1024 * 1024) {
        docs.push(`'${f.name}' is ${(f.size / 1024 / 1024).toFixed(1)} MB; the limit is ${maxMb} MB.`);
      }
    }
    if (docs.length) found.documents = docs;
    return found;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    const found = validate();
    setErrors(found);
    if (found.company_name || found.documents) return;
    const form = new FormData();
    form.append("company_name", company.trim());
    selected.forEach((id) => form.append("policy_ids", id));
    files.forEach((f) => form.append("files", f, f.name));
    setSubmitting(true);
    try {
      const { job_id } = await api.createRun(form);
      navigate(`/generating/${job_id}`, { state: { companyName: company.trim() } });
    } catch (e) {
      const err = e as ApiError;
      if (Object.keys(err.fields ?? {}).length) setErrors(err.fields as Errors);
      else setFormError(err.message);
      setSubmitting(false);
    }
  }

  const summaryItems = [
    ...(errors.company_name ?? []).map((m) => ({ href: `#${nameId}`, m })),
    ...(errors.documents ?? []).map((m) => ({ href: "#documents", m })),
    ...(formError ? [{ href: "#submit", m: formError }] : []),
  ];

  return (
    <div className="page container">
      <header className="page-head">
        <p className="eyebrow">Step 1 of 3 · New pitch</p>
        <h1 className="display">Create a client pitch</h1>
        <p className="lead">
          Name the client and choose the policy documents to compare. TRACE researches the company, recommends one
          policy, writes the deck and checks every statement against its source.
        </p>
      </header>

      <form className="form" onSubmit={submit} noValidate aria-describedby={hasErrors ? "form-errors" : undefined}>
        {summaryItems.length > 0 && (
          <div className="error-summary" id="form-errors" role="alert" tabIndex={-1} ref={summaryRef}>
            <h2>Please check the following</h2>
            <ul>
              {summaryItems.map((item) => (
                <li key={item.m}>
                  <a href={item.href}>{item.m}</a>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="field">
          <label className="label" htmlFor={nameId}>
            Client company
          </label>
          <p className="hint" id={nameHint}>
            The company you are pitching to, for example its registered or trading name.
          </p>
          <input
            id={nameId}
            className="input input--lg"
            name="company_name"
            autoComplete="organization"
            value={company}
            maxLength={MAX_NAME + 20}
            onChange={(e) => {
              setCompany(e.target.value);
              if (errors.company_name) setErrors((x) => ({ ...x, company_name: undefined }));
            }}
            aria-invalid={Boolean(errors.company_name)}
            aria-describedby={`${nameHint}${errors.company_name ? ` ${nameErr}` : ""}`}
            required
          />
          {errors.company_name && (
            <p className="field-error" id={nameErr}>
              <WarningCircle size={16} weight="bold" aria-hidden="true" />
              {errors.company_name[0]}
            </p>
          )}
        </div>

        <fieldset className="fieldset" id="documents" aria-describedby={errors.documents ? docsErr : undefined}>
          <legend>
            <span className="label">Policy documents</span>
          </legend>
          <p className="hint">The recommendation is chosen from these documents only. All four brochures are selected.</p>

          {loadError && (
            <div className="notice notice--error" role="alert">
              <WarningCircle size={18} weight="bold" aria-hidden="true" />
              <span>{loadError}</span>
            </div>
          )}
          {!policies && !loadError && (
            <p className="loading-line">
              <span className="spinner" aria-hidden="true" /> Loading the policy library…
            </p>
          )}

          {(policies?.length || files.length > 0) && (
            <ul className="doc-list">
              {policies?.map((p) => (
                <li className="doc-row" key={p.id}>
                  <label>
                    <input
                      type="checkbox"
                      className="checkbox"
                      checked={selected.has(p.id)}
                      onChange={() => toggle(p.id)}
                    />
                    <span className="doc-row__body">
                      <span className="doc-row__name">{p.name}</span>
                      <span className="doc-row__kind">Product brochure · Policy library</span>
                    </span>
                  </label>
                </li>
              ))}
              {files.map((f) => (
                <li className="doc-row" key={`${f.name}:${f.size}`}>
                  <div className="doc-row__static">
                    <FilePdf size={22} aria-hidden="true" />
                    <span className="doc-row__body">
                      <span className="doc-row__name">{f.name}</span>
                      <span className="doc-row__kind">Uploaded · {(f.size / 1024 / 1024).toFixed(1)} MB</span>
                    </span>
                    <button
                      type="button"
                      className="icon-btn doc-row__remove"
                      onClick={() => setFiles((all) => all.filter((x) => x !== f))}
                      aria-label={`Remove ${f.name}`}
                    >
                      <X size={16} weight="bold" aria-hidden="true" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}

          <div
            className={`dropzone${dragging ? " dropzone--active" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <div className="dropzone__row">
              <button type="button" className="btn btn--secondary btn--small" onClick={() => fileInput.current?.click()}>
                <UploadSimple size={16} weight="bold" aria-hidden="true" />
                Upload a policy PDF
              </button>
              <span className="hint">or drop files here · PDF, up to {maxMb} MB each</span>
            </div>
            <input
              ref={fileInput}
              type="file"
              accept="application/pdf,.pdf"
              multiple
              hidden
              onChange={(e) => {
                addFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </div>

          {errors.documents && (
            <div id={docsErr} className="field-error" role="alert">
              <WarningCircle size={16} weight="bold" aria-hidden="true" />
              <span>{errors.documents.join(" ")}</span>
            </div>
          )}
        </fieldset>

        <div className="form-actions">
          <button id="submit" type="submit" className="btn btn--lg" disabled={submitting}>
            {submitting ? (
              <>
                <span className="spinner" aria-hidden="true" /> Starting…
              </>
            ) : (
              <>
                Generate pitch
                <ArrowRight className="arrow" size={20} weight="bold" aria-hidden="true" />
              </>
            )}
          </button>
          <p className="hint" style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Info size={16} aria-hidden="true" /> Generation usually takes a few minutes.
          </p>
        </div>
      </form>
    </div>
  );
}
