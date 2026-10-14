"use client";

import { useEffect, useRef, useState } from "react";
import { FamilyError, familyApi } from "@/lib/family";
import type { Contact, KnownService, Profile } from "@/lib/family";
import { BookIcon, GlobeIcon, MailIcon, PencilIcon, PhoneIcon, PlusIcon, TrashIcon, UserIcon } from "./icons";

const ALERT_ROLES = ["son", "daughter", "child"];
const RELATIONSHIPS = ["son", "daughter", "child", "grandson", "granddaughter", "friend", "neighbour", "other"];
const KINDS = ["bank", "pharmacy", "clinic", "delivery", "utility", "shop", "other"];

const lines = (values: string[]) => values.join("\n");
const splitList = (text: string) =>
  text
    .split(/[\n,;]+/)
    .map((v) => v.trim())
    .filter(Boolean);

type Editing = { kind: "contact" | "service"; index: number } | null;

function ContactForm({ initial, onSave, onCancel, saving, error }: FormProps<Contact>) {
  const [name, setName] = useState(initial.name);
  const [relationship, setRelationship] = useState(initial.relationship);
  const [phones, setPhones] = useState(lines(initial.phones));
  const [emails, setEmails] = useState(lines(initial.emails));
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => ref.current?.focus(), []);
  const alerts = ALERT_ROLES.includes(relationship.trim().toLowerCase());
  return (
    <form
      className="stack"
      onSubmit={(e) => {
        e.preventDefault();
        onSave({ name, relationship, phones: splitList(phones), emails: splitList(emails) });
      }}
    >
      <div className="fgrid fgrid--2">
        <div className="ffield">
          <label htmlFor="c-name">Name</label>
          <input id="c-name" ref={ref} className="finput" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" required />
        </div>
        <div className="ffield">
          <label htmlFor="c-rel">Relationship</label>
          <input id="c-rel" className="finput" list="relationships" value={relationship} onChange={(e) => setRelationship(e.target.value)} autoComplete="off" />
          <datalist id="relationships">
            {RELATIONSHIPS.map((r) => (
              <option key={r} value={r} />
            ))}
          </datalist>
          <span className="hint">{alerts ? "A son, daughter or child gets the family alerts." : "Son, daughter or child gets the family alerts."}</span>
        </div>
      </div>
      <div className="fgrid fgrid--2">
        <div className="ffield">
          <label htmlFor="c-phones">Phone numbers</label>
          <textarea id="c-phones" className="ftextarea" value={phones} onChange={(e) => setPhones(e.target.value)} placeholder={"212-555-0101\n646-555-0101"} />
          <span className="hint">One per line. Messages from these numbers count as trusted.</span>
        </div>
        <div className="ffield">
          <label htmlFor="c-emails">Email addresses</label>
          <textarea id="c-emails" className="ftextarea" value={emails} onChange={(e) => setEmails(e.target.value)} placeholder="david@example.com" />
          <span className="hint">Alerts go here, and mail from here counts as trusted.</span>
        </div>
      </div>
      {error && <p className="notice notice--bad" role="alert">{error}</p>}
      <div className="row">
        <button type="submit" className="fbtn fbtn--primary" disabled={saving}>
          {saving ? "Saving…" : "Save contact"}
        </button>
        <button type="button" className="fbtn fbtn--quiet" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function ServiceForm({ initial, onSave, onCancel, saving, error }: FormProps<KnownService>) {
  const [name, setName] = useState(initial.name);
  const [kind, setKind] = useState(initial.kind);
  const [domains, setDomains] = useState(lines(initial.domains));
  const [phones, setPhones] = useState(lines(initial.phones));
  const [senders, setSenders] = useState(lines(initial.senders));
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => ref.current?.focus(), []);
  return (
    <form
      className="stack"
      onSubmit={(e) => {
        e.preventDefault();
        onSave({ name, kind, domains: splitList(domains), phones: splitList(phones), senders: splitList(senders) });
      }}
    >
      <div className="fgrid fgrid--2">
        <div className="ffield">
          <label htmlFor="s-name">Name</label>
          <input id="s-name" ref={ref} className="finput" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" required />
        </div>
        <div className="ffield">
          <label htmlFor="s-kind">Kind</label>
          <input id="s-kind" className="finput" list="kinds" value={kind} onChange={(e) => setKind(e.target.value)} autoComplete="off" />
          <datalist id="kinds">
            {KINDS.map((k) => (
              <option key={k} value={k} />
            ))}
          </datalist>
        </div>
      </div>
      <div className="ffield">
        <label htmlFor="s-domains">Website addresses</label>
        <textarea id="s-domains" className="ftextarea" value={domains} onChange={(e) => setDomains(e.target.value)} placeholder="lakeviewbank.example.com" />
        <span className="hint">One per line. A link or sender that only imitates one of these is flagged as a look-alike.</span>
      </div>
      <div className="fgrid fgrid--2">
        <div className="ffield">
          <label htmlFor="s-phones">Phone numbers</label>
          <textarea id="s-phones" className="ftextarea" value={phones} onChange={(e) => setPhones(e.target.value)} placeholder="212-555-0110" />
        </div>
        <div className="ffield">
          <label htmlFor="s-senders">Sender addresses</label>
          <textarea id="s-senders" className="ftextarea" value={senders} onChange={(e) => setSenders(e.target.value)} placeholder="no-reply@lakeviewbank.example.com" />
        </div>
      </div>
      {error && <p className="notice notice--bad" role="alert">{error}</p>}
      <div className="row">
        <button type="submit" className="fbtn fbtn--primary" disabled={saving}>
          {saving ? "Saving…" : "Save service"}
        </button>
        <button type="button" className="fbtn fbtn--quiet" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
    </form>
  );
}

interface FormProps<T> {
  initial: T;
  onSave: (value: T) => void;
  onCancel: () => void;
  saving: boolean;
  error: string | null;
}

function Facts({ icon, items }: { icon: React.ReactNode; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <li>
      {icon}
      <span>{items.join(" · ")}</span>
    </li>
  );
}

function RemoveButtons({ name, onRemove, confirming, setConfirming, disabled }: { name: string; onRemove: () => void; confirming: boolean; setConfirming: (v: boolean) => void; disabled: boolean }) {
  if (!confirming) {
    return (
      <button type="button" className="fbtn fbtn--danger fbtn--small" onClick={() => setConfirming(true)} aria-label={`Remove ${name}`} disabled={disabled}>
        <TrashIcon />
        Remove
      </button>
    );
  }
  return (
    <>
      <button type="button" className="fbtn fbtn--danger fbtn--small" onClick={onRemove} disabled={disabled}>
        <TrashIcon />
        Yes, remove {name}
      </button>
      <button type="button" className="fbtn fbtn--quiet fbtn--small" onClick={() => setConfirming(false)}>
        Keep
      </button>
    </>
  );
}

interface Props {
  parentId: string;
  parentFirstName: string;
  onUnauthorized: () => void;
}

export function MemoryPanel({ parentId, parentFirstName, onUnauthorized }: Props) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    familyApi
      .profile(parentId)
      .then((p) => live && setProfile(p))
      .catch((e: unknown) => {
        if (e instanceof FamilyError && e.status === 401) return onUnauthorized();
        if (live) setLoadError(e instanceof FamilyError ? e.message : "Could not load her memory.");
      });
    return () => {
      live = false;
    };
  }, [parentId, onUnauthorized]);

  async function persist(next: Profile, message: string) {
    setSaving(true);
    setFormError(null);
    try {
      const stored = await familyApi.saveProfile(parentId, next);
      setProfile(stored);
      setEditing(null);
      setConfirm(null);
      setSaved(message);
      setTimeout(() => setSaved((cur) => (cur === message ? null : cur)), 6000);
    } catch (e) {
      if (e instanceof FamilyError && e.status === 401) return onUnauthorized();
      setFormError(e instanceof FamilyError ? e.message : "Could not save. Try again.");
    } finally {
      setSaving(false);
    }
  }

  if (loadError) return <p className="notice notice--bad">{loadError}</p>;
  if (!profile) return <p className="muted">Loading…</p>;

  const empty: Contact = { name: "", relationship: "", phones: [], emails: [] };
  const emptyService: KnownService = { name: "", kind: "", domains: [], phones: [], senders: [] };
  const replace = <T,>(list: T[], index: number, value: T) => (index < 0 ? [...list, value] : list.map((x, i) => (i === index ? value : x)));
  const open = (kind: "contact" | "service", index: number) => {
    setEditing({ kind, index });
    setFormError(null);
    setConfirm(null);
  };

  return (
    <div>
      <p className="muted" style={{ marginBottom: 16, maxWidth: "46em" }}>
        This is what ScamShield knows about {parentFirstName}’s world. A message from a trusted contact or a known service scores lower, and an unknown
        number asking for money scores higher. Changes apply from the next check.
      </p>
      <div className="mem-grid">
        <section className="card" aria-labelledby="contacts-title">
          <div className="mem-head">
            <div>
              <h3 id="contacts-title" style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <UserIcon style={{ width: 24, height: 24 }} />
                Trusted contacts
              </h3>
              <p>People {parentFirstName} really hears from.</p>
            </div>
            <button type="button" className="fbtn fbtn--primary fbtn--small" onClick={() => open("contact", -1)}>
              <PlusIcon />
              Add
            </button>
          </div>
          <ul className="entries">
            {editing?.kind === "contact" && editing.index === -1 && (
              <li className="entry entry--editing">
                <ContactForm initial={empty} saving={saving} error={formError} onCancel={() => setEditing(null)} onSave={(c) => persist({ ...profile, trusted_contacts: replace(profile.trusted_contacts, -1, c) }, `${c.name.trim() || "Contact"} added.`)} />
              </li>
            )}
            {profile.trusted_contacts.map((c, i) => {
              const isEditing = editing?.kind === "contact" && editing.index === i;
              return (
                <li key={`${c.name}-${i}`} className={`entry${isEditing ? " entry--editing" : ""}`}>
                  {isEditing ? (
                    <ContactForm initial={c} saving={saving} error={formError} onCancel={() => setEditing(null)} onSave={(next) => persist({ ...profile, trusted_contacts: replace(profile.trusted_contacts, i, next) }, `${next.name.trim() || "Contact"} saved.`)} />
                  ) : (
                    <>
                      <div className="entry-top">
                        <h4>{c.name}</h4>
                        {c.relationship && <span className="chip">{c.relationship}</span>}
                        {ALERT_ROLES.includes(c.relationship.trim().toLowerCase()) && <span className="chip chip--amber">Gets alerts</span>}
                      </div>
                      <ul className="facts">
                        <Facts icon={<PhoneIcon />} items={c.phones} />
                        <Facts icon={<MailIcon />} items={c.emails} />
                        {c.phones.length === 0 && c.emails.length === 0 && <li className="muted">No phone or email yet, so ScamShield cannot recognise them.</li>}
                      </ul>
                      <div className="entry-actions">
                        <button type="button" className="fbtn fbtn--quiet fbtn--small" onClick={() => open("contact", i)} aria-label={`Edit ${c.name}`}>
                          <PencilIcon />
                          Edit
                        </button>
                        <RemoveButtons
                          name={c.name}
                          confirming={confirm === `c${i}`}
                          setConfirming={(v) => setConfirm(v ? `c${i}` : null)}
                          disabled={saving}
                          onRemove={() => persist({ ...profile, trusted_contacts: profile.trusted_contacts.filter((_, j) => j !== i) }, `${c.name} removed.`)}
                        />
                      </div>
                    </>
                  )}
                </li>
              );
            })}
            {profile.trusted_contacts.length === 0 && editing?.index !== -1 && <li className="muted">No trusted contacts yet. Add the people she hears from.</li>}
          </ul>
        </section>

        <section className="card" aria-labelledby="services-title">
          <div className="mem-head">
            <div>
              <h3 id="services-title" style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <BookIcon style={{ width: 24, height: 24 }} />
                Known services
              </h3>
              <p>Her bank, pharmacy, clinic and shops. Look-alikes of their addresses are caught.</p>
            </div>
            <button type="button" className="fbtn fbtn--primary fbtn--small" onClick={() => open("service", -1)}>
              <PlusIcon />
              Add
            </button>
          </div>
          <ul className="entries">
            {editing?.kind === "service" && editing.index === -1 && (
              <li className="entry entry--editing">
                <ServiceForm initial={emptyService} saving={saving} error={formError} onCancel={() => setEditing(null)} onSave={(s) => persist({ ...profile, known_services: replace(profile.known_services, -1, s) }, `${s.name.trim() || "Service"} added.`)} />
              </li>
            )}
            {profile.known_services.map((s, i) => {
              const isEditing = editing?.kind === "service" && editing.index === i;
              return (
                <li key={`${s.name}-${i}`} className={`entry${isEditing ? " entry--editing" : ""}`}>
                  {isEditing ? (
                    <ServiceForm initial={s} saving={saving} error={formError} onCancel={() => setEditing(null)} onSave={(next) => persist({ ...profile, known_services: replace(profile.known_services, i, next) }, `${next.name.trim() || "Service"} saved.`)} />
                  ) : (
                    <>
                      <div className="entry-top">
                        <h4>{s.name}</h4>
                        {s.kind && <span className="chip">{s.kind}</span>}
                      </div>
                      <ul className="facts">
                        <Facts icon={<GlobeIcon />} items={s.domains} />
                        <Facts icon={<PhoneIcon />} items={s.phones} />
                        <Facts icon={<MailIcon />} items={s.senders} />
                      </ul>
                      <div className="entry-actions">
                        <button type="button" className="fbtn fbtn--quiet fbtn--small" onClick={() => open("service", i)} aria-label={`Edit ${s.name}`}>
                          <PencilIcon />
                          Edit
                        </button>
                        <RemoveButtons
                          name={s.name}
                          confirming={confirm === `s${i}`}
                          setConfirming={(v) => setConfirm(v ? `s${i}` : null)}
                          disabled={saving}
                          onRemove={() => persist({ ...profile, known_services: profile.known_services.filter((_, j) => j !== i) }, `${s.name} removed.`)}
                        />
                      </div>
                    </>
                  )}
                </li>
              );
            })}
            {profile.known_services.length === 0 && editing?.index !== -1 && <li className="muted">No services yet.</li>}
          </ul>
        </section>
      </div>
      {saved && (
        <p className="notice notice--good toast" role="status">
          <span>{saved} ScamShield uses it from the next check.</span>
        </p>
      )}
    </div>
  );
}
