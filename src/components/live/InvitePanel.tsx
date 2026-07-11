"use client";

import { useState, useSyncExternalStore } from "react";
import { Avatar, CopyButton, StatusPill } from "./primitives";
import { participantLink, personaById, qrImageUrl } from "@/lib/demo";
import type { Member } from "@/lib/orkestr-api";
import s from "./live.module.css";

// Origin is only known on the client. The server snapshot is empty so SSR and
// the first hydration render agree; after hydration the client snapshot fills
// it in — keeps invite links absolute (scannable / shareable) with no mismatch.
const subscribeNoop = () => () => {};
function useOrigin(): string {
  return useSyncExternalStore(
    subscribeNoop,
    () => window.location.origin,
    () => "",
  );
}

// Deterministic faux-QR pattern so the card still looks like a QR code offline
// (the real, scannable QR is an <img>; this renders only if that image fails).
function fauxCells(seed: string, n = 11): boolean[] {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  const out: boolean[] = [];
  for (let i = 0; i < n * n; i++) {
    h ^= h << 13;
    h ^= h >>> 17;
    h ^= h << 5;
    out.push((h & 7) > 3);
  }
  return out;
}

function QrTile({ link, seed, ready }: { link: string; seed: string; ready: boolean }) {
  const [ok, setOk] = useState(true);
  return (
    <div className={s.qrTile} aria-hidden>
      {ok && ready ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          className={s.qrImg}
          src={qrImageUrl(link, 168)}
          alt=""
          width={168}
          height={168}
          loading="lazy"
          onError={() => setOk(false)}
        />
      ) : (
        <div className={s.qrFaux}>
          {fauxCells(seed).map((on, i) => (
            <span key={i} className={on ? s.qrOn : s.qrOff} />
          ))}
        </div>
      )}
    </div>
  );
}

function InviteCard({
  member,
  roomId,
  ready,
  origin,
}: {
  member: Member;
  roomId: string;
  ready: boolean;
  origin: string;
}) {
  const persona = personaById(member.id);
  const link = participantLink(roomId, member.id, origin);

  return (
    <div className={`${s.inviteCard} ${ready ? s.inviteCardReady : ""}`}>
      <div className={s.inviteCardHead}>
        <Avatar id={member.id} size={36} ring={ready} />
        <div className={s.inviteWho}>
          <strong>{member.name}</strong>
          <span>{persona?.vibe ?? "Friend"}</span>
        </div>
        <StatusPill kind={ready ? "ready" : "pending"}>{ready ? "Ready" : "Pending"}</StatusPill>
      </div>

      <QrTile link={link} seed={`${roomId}:${member.id}`} ready={Boolean(origin)} />
      <span className={s.qrCaption}>Scan to join as {member.name}</span>

      <div className={s.inviteActions}>
        <CopyButton value={link} label="Copy link" />
        <a className={s.inviteOpen} href={link} target="_blank" rel="noreferrer">
          Open as {member.name} ↗
        </a>
      </div>
    </div>
  );
}

export function InvitePanel({
  roomId,
  members,
  readyIds,
  localRoom = false,
  liveMode = false,
}: {
  roomId: string | null;
  members: Member[];
  readyIds: string[];
  localRoom?: boolean;
  liveMode?: boolean;
}) {
  const origin = useOrigin();
  // Only reached in the brief window before a room id exists. Never a dead-end:
  // the room is created automatically, so this reads as setup, not an error.
  if (!roomId) {
    return (
      <div className={s.invitePanel}>
        <div className={s.inviteHead}>
          <div>
            <h3 className={s.inviteTitle}>Invite friends</h3>
            <span className={s.inviteSub}>
              Setting up your live room — invite links appear in a moment.
            </span>
          </div>
          <span className={s.roomChip}>
            <span className={s.roomChipDot} aria-hidden /> Opening…
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className={s.invitePanel}>
      <div className={s.inviteHead}>
        <div>
          <h3 className={s.inviteTitle}>Invite friends</h3>
          <span className={s.inviteSub}>
            Each friend opens their own link on their phone. Scan the code, or tap “Open as” to
            open from this device.
          </span>
          <span className={s.inviteGuest}>
            Friends join as guests — no account, no wallet, no app install. Under 30 seconds each.
          </span>
          {localRoom && !liveMode ? (
            <span className={s.inviteHint}>
              Live sync is warming up. Demo links still work locally.
            </span>
          ) : null}
        </div>
        <span className={s.roomChip}>Room {roomId}</span>
      </div>

      <div className={s.inviteGrid}>
        {members.map((m) => (
          <InviteCard
            key={m.id}
            member={m}
            roomId={roomId}
            origin={origin}
            ready={readyIds.includes(m.id)}
          />
        ))}
      </div>
    </div>
  );
}
