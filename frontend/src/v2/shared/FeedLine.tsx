interface FeedLineProps {
  killSwitchActive: boolean;
  ehealthDown?: boolean;
}

export function FeedLine({ killSwitchActive, ehealthDown }: FeedLineProps) {
  if (!killSwitchActive && !ehealthDown) return null;
  return (
    <div className="v2-feed-line" role="status" data-testid="feed-health-banner">
      <span>⚠</span>
      <span>
        {killSwitchActive
          ? "AI drafting is off. Every item is manual."
          : "eHealth feed is not reachable — external allergy history is incomplete and does not count as all clear."}
      </span>
    </div>
  );
}
