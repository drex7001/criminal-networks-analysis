import type { LandingResult } from "../api/client";
import { Digest } from "./Digest";

/**
 * What the request did, in its own words — rendered above the register rather
 * than under the form.
 *
 * It sits with the records because it is a receipt for one: landing is a
 * moment, the record is the thing, and putting the confirmation next to the row
 * it created means the operator reads both without moving. Under a seven-field
 * form it fell below the fold, which made the one piece of feedback this screen
 * exists to give the easiest thing on it to miss.
 */
export function Outcome({ result }: { result: LandingResult }) {
  const { outcome, record } = result;
  const filename = String(record.provenance?.["original_filename"] ?? record.record_id);

  const copy = {
    landed: {
      title: "Landed",
      body: `${filename} is in the vault.`,
    },
    already_landed: {
      title: "Already landed",
      body: "These exact bytes are already recorded under this name. Nothing was added.",
    },
    quarantined: {
      title: "Quarantined",
      body: record.quarantine_reason ?? "Held for review.",
    },
  }[outcome];

  return (
    <div
      className={`outcome outcome--${outcome.replace("_", "-")}`}
      role="status"
      data-testid="intake-outcome"
      data-outcome={outcome}
    >
      <div className="outcome__head">
        <strong>{copy.title}</strong>
        <Digest hash={record.content_hash} />
      </div>
      <p>{copy.body}</p>
      {outcome === "quarantined" && (
        <p className="muted">A supervisor releases it before anything can read it.</p>
      )}
    </div>
  );
}
