import { useState } from "react";

import type { LandingResult } from "../api/client";
import { IntakePanel } from "./IntakePanel";
import { Outcome } from "./Outcome";
import { RecordLedger } from "./RecordLedger";

/**
 * Source landing and extraction status (T23a, spec 07 §6 "Source landing").
 *
 * Intake on the left, the register on the right, because that is the order the
 * work happens in and because the register is what you come back to: landing is
 * a moment, and everything after it — quarantine, derivative, extraction — is a
 * state you check. The outcome of the last landing belongs on the register
 * side, next to the record it produced.
 */
export function SourcesView() {
  const [outcome, setOutcome] = useState<LandingResult | null>(null);

  return (
    <div className="sources">
      <IntakePanel onLanded={setOutcome} />
      <div className="sources__records">
        {outcome && <Outcome result={outcome} />}
        <RecordLedger />
      </div>
    </div>
  );
}
