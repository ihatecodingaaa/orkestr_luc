import { Avatar } from "./primitives";
import { personName } from "@/lib/demo";
import s from "./live.module.css";

export type ActivityItem = {
  id: string;
  kind: "chat" | "system" | "nudge" | "reaction";
  who?: string;
  text: string;
};

export function ActivityFeed({
  items,
  title = "Group chat",
}: {
  items: ActivityItem[];
  title?: string;
}) {
  return (
    <div className={s.feed}>
      <div className={s.feedHead}>
        <h3 className={s.feedTitle}>{title}</h3>
        <span className={s.feedLive}>
          <span className={s.feedLiveDot} aria-hidden />
          live
        </span>
      </div>
      <ul className={s.feedList}>
        {items.map((item) => {
          const system = item.kind === "system" || item.kind === "nudge";
          return (
            <li key={item.id} className={`${s.feedItem} ${system ? s.feedSystem : ""}`}>
              {system ? (
                <span className={s.feedBadge} aria-hidden>
                  ◆
                </span>
              ) : (
                <Avatar id={item.who} size={28} />
              )}
              <div className={s.feedBody}>
                {item.kind === "chat" && item.who ? (
                  <strong className={s.feedAuthor}>{personName(item.who)}</strong>
                ) : null}
                <span>{item.text}</span>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
