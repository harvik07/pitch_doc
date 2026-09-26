import { useEffect, useId, useRef, useState, type DragEvent, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ArrowRight, FilePdf, Info, UploadSimple, WarningCircle, X } from "@phosphor-icons/react";
import { api, ApiError, type Policy } from "../api";
import { usePageTitle } from "../components/usePageTitle";

interface Errors {
  company_name?: string[];
  policy?: string[];
  uploads?: string[];
}

const MAX_NAME = 120;

/** Create: the client company, exactly ONE policy from the library (radio buttons; the API enforces it too) and
 * any number of additional policy PDFs. */
export default function CreatePitch() {
  usePageTitle("Create a pitch");
  const navigate = useNavigate();
  const location = useLocation();
  const initialName = (location.state as { companyName?: string } | null)?.companyName ?? "";

  const [policies, setPolicies] = useState<Policy[] | null>(null);
  const [maxMb, setMaxMb] = useState(25);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [company, setCompany] = useState(initialName);
  const [policy, setPolicy] = useState<string>("");
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
  const policyErr = useId();
  const uploadErr = useId();

  useEffect(() => {
    api
      .policies()
      .then((data) => {
        setPolicies(data.policies);
        setMaxMb(data.max_file_mb);
      })
      .catch((e: ApiError) => setLoadError(e.message));
  }, []);

  const hasErrors = Boolean(errors.company_name?.length || errors.policy?.length || errors.uploads?.length || formError);

  useEffect(() => {
    if (hasErrors) summaryRef.current?.focus();
  }, [hasErrors, errors, formError]);

  function addFiles(list: FileList | null) {
    if (!list) return;
    const incoming = Array.from(list);
    setFiles((current) => {
      const names = new Set(current.map((f) => `${f.name}:${f.size}`));
      return [...current, ...incoming.filter((f) => !names.has(`${f.name}:${f.size}`))];
    });
    setErrors((e) => ({ ...e, uploads: undefined }));
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
    if (!policy) found.policy = ["Please select one policy from the policy library."];
    const uploads: string[] = [];
    for (const f of files) {
      if (!f.name.toLowerCase().endsWith(".pdf")) uploads.push(`'${f.name}' isn't a PDF. Please add policy documents as PDF files.`);
      else if (f.size === 0) uploads.push(`'${f.name}' is empty (0 bytes).`);
      else if (f.size > maxMb * 1024 * 1024) {
        uploads.push(`'${f.name}' is ${(f.size / 1024 / 1024).toFixed(1)} MB; the limit is ${maxMb} MB.`);
      }
    }
    if (uploads.length) found.uploads = uploads;
    return found;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    const found = validate();
    setErrors(found);
    if (found.company_name || found.policy || found.uploads) return;
    const form = new FormData();
    form.append("company_name", company.trim());
    form.append("policy_ids", policy);
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
    ...(errors.policy ?? []).map((m) => ({ href: "#policy-library", m })),
    ...(errors.uploads ?? []).map((m) => ({ href: "#uploads", m })),
    ...(formError ? [{ href: "#submit", m: formError }] : []),
  ];

  return (
    <div className="page container">
      <header className="page-head">
        <p className="eyebrow">Step 1 of 3 · New pitch</p>
        <h1 className="display">Create a client pitch</h1>
        <p className="lead">
          Name the client, choose a policy from the library and, if you have them, add further policy PDFs. TRACE
          researches the company, recommends one policy, writes the deck and checks every statement against its source.
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
            The company you are pitching to, for example Infosys.
          </p>
          <input
            id={nameId}
            className="input input--lg"
            name="company_name"
            autoComplete="organization"
            placeholder="Example: Infosys"
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

        <fieldset
          className="fieldset"
          id="policy-library"
          aria-describedby={errors.policy ? policyErr : undefined}
          aria-invalid={Boolean(errors.policy)}
        >
          <legend>
            <span className="label">Policy from the library</span>
          </legend>
          <p className="hint">Select exactly one policy brochure.</p>

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
          {policies && policies.length > 0 && (
            <ul className="doc-list">
              {policies.map((p) => (
                <li className="doc-row" key={p.id}>
                  <label>
                    <input
                      type="radio"
                      name="bundled-policy"
                      className="radio-input"
                      value={p.id}
                      checked={policy === p.id}
                      onChange={() => {
                        setPolicy(p.id);
                        setErrors((e) => ({ ...e, policy: undefined }));
                      }}
                    />
                    <span className="doc-row__body">
                      <span className="doc-row__name">{p.name}</span>
                      <span className="doc-row__kind">Product brochure · Policy library</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          {errors.policy && (
            <p id={policyErr} className="field-error" role="alert">
              <WarningCircle size={16} weight="bold" aria-hidden="true" />
              <span>{errors.policy[0]}</span>
            </p>
          )}
        </fieldset>

        <fieldset className="fieldset" id="uploads" aria-describedby={errors.uploads ? uploadErr : undefined}>
          <legend>
            <span className="label">
              Additional policy PDFs <span className="muted">(optional)</span>
            </span>
          </legend>
          <p className="hint">Upload as many policy documents as you need; each is compared with the library policy.</p>

          {files.length > 0 && (
            <ul className="doc-list" aria-label="Uploaded policy PDFs">
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
                Upload policy PDFs
              </button>
              <span className="hint">or drop files here · several PDFs allowed, up to {maxMb} MB each</span>
            </div>
            <input
              ref={fileInput}
              type="file"
              accept="application/pdf,.pdf"
              multiple
              hidden
              data-testid="upload-input"
              onChange={(e) => {
                addFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </div>

          {errors.uploads && (
            <div id={uploadErr} className="field-error" role="alert">
              <WarningCircle size={16} weight="bold" aria-hidden="true" />
              <span>{errors.uploads.join(" ")}</span>
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
