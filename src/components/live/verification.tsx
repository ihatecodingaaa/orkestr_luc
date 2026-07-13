import type { VenueVerification, VerificationStatus } from "@/lib/orkestr-api";
import s from "./live.module.css";

const LABEL: Record<VerificationStatus, string> = {
  verified: "Google Places verified",
  source_only: "Web source found",
  // Matches the backend badge (agents/venue_grounding.py): the seeded hero room
  // shows honest sample-data labelling, never implying a verified live venue.
  demo: "Seeded demo venue · sample data",
  unverified: "Unverified",
};

const ICON: Record<VerificationStatus, string> = {
  verified: "✓",
  source_only: "↗",
  demo: "◐",
  unverified: "!",
};

export function verificationLabel(status?: string): string {
  return LABEL[status as VerificationStatus] ?? LABEL.unverified;
}

export function normStatus(v?: VenueVerification): VerificationStatus {
  return (v?.verification_status as VerificationStatus) ?? "unverified";
}

// Trust badge — status is never conveyed by color alone (icon + text label).
export function VenueBadge({
  verification,
  small = false,
}: {
  verification?: VenueVerification;
  small?: boolean;
}) {
  const status = normStatus(verification);
  const label = verification?.badge ?? verificationLabel(status);
  return (
    <span
      className={`${s.vBadge} ${s[`vBadge_${status}`]} ${small ? s.vBadgeSm : ""}`}
      title={verification?.verification_notes || label}
    >
      <span className={s.vBadgeDot} aria-hidden>
        {ICON[status]}
      </span>
      {label}
    </span>
  );
}

// Evidence strip: rating, source domain, and the real "view" links when present.
export function VenueEvidence({ verification }: { verification?: VenueVerification }) {
  if (!verification) return null;
  const {
    rating,
    user_rating_count: count,
    source_domain: domain,
    source_url: sourceUrl,
    google_maps_uri: mapsUri,
    google_place_id: placeId,
  } = verification;

  const hasRating = typeof rating === "number";
  const hasLinks = Boolean(mapsUri || sourceUrl);
  if (!hasRating && !domain && !hasLinks) return null;

  return (
    <div className={s.vEvidence}>
      {hasRating ? (
        <span className={s.vEvidenceItem}>
          <span className={s.vStar} aria-hidden>
            ★
          </span>
          {rating!.toFixed(1)}
          {typeof count === "number" ? <span className={s.vMuted}> ({count.toLocaleString()})</span> : null}
        </span>
      ) : null}
      {domain ? (
        <span className={s.vEvidenceItem}>
          <span className={s.vMuted}>source</span> {domain}
        </span>
      ) : null}
      {placeId ? (
        <span className={s.vEvidenceItem}>
          <span className={s.vMuted}>place id</span> {placeId.slice(0, 12)}…
        </span>
      ) : null}
      <span className={s.vEvidenceLinks}>
        {mapsUri ? (
          <a className={s.vLink} href={mapsUri} target="_blank" rel="noreferrer">
            View on Google Maps ↗
          </a>
        ) : null}
        {sourceUrl && sourceUrl !== mapsUri ? (
          <a className={s.vLink} href={sourceUrl} target="_blank" rel="noreferrer">
            View source ↗
          </a>
        ) : null}
      </span>
    </div>
  );
}

// Polished empty state when strict grounding finds no real restaurant.
export function NoVerifiedVenueCard({ message }: { message?: string }) {
  return (
    <div className={s.noVenueCard} role="status">
      <div className={s.noVenueIcon} aria-hidden>
        🔎
      </div>
      <h2 className={s.noVenueTitle}>No verified restaurant found</h2>
      <p className={s.noVenueSub}>
        {message ?? "Try relaxing budget, location, or dietary constraints."}
      </p>
      <span className={s.demoTag}>
        Strict mode is on — Orkestr won&apos;t show a place it can&apos;t verify.
      </span>
    </div>
  );
}
