/**
 * A content hash, rendered as the identity it is.
 *
 * This is the one place the workspace uses monospace, and the rule is
 * deliberate: monospace here means "an exact identifier, comparable character
 * by character", which is precisely what a sha256 is for and precisely what
 * prose is not. Everything a person wrote stays in the body face.
 *
 * It carries the explanatory weight on the landing screen. "Already landed" is
 * only convincing if you can see that the digest coming back is the digest of
 * the thing you just sent, so the same object appears in the confirmation and
 * in the register row it points at.
 */
export function Digest({ hash, label = "sha256" }: { hash: string; label?: string }) {
  return (
    <code className="digest" title={`${label}:${hash}`}>
      <span className="digest__label">{label}</span>
      {hash.slice(0, 12)}
    </code>
  );
}
