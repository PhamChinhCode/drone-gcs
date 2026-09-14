// Màn hình Vận hành 3D (mặc định, 8.2): cảnh 3D toàn màn hình, HUD góc trên trái, tag bên trái, panel bên phải.
import { useEffect, useState } from "react";
import { get } from "../../lib/api";
import { liveBuffer, liveClock } from "../../lib/ws";
import type { Mission } from "../../lib/types";
import { useLive } from "../../store/live";
import { useSite } from "../../store/site";
import { Scene } from "../scene3d/Scene";
import { KIND_COLOR } from "../scene3d/objects/labels";
import { Hud } from "./Hud";
import { ManualControl } from "./ManualControl";
import { TelemetryPanel } from "./TelemetryPanel";

export function OperationPage() {
  const { tags, areas } = useSite();
  const shadow = useLive((s) => s.shadow);
  const tagDetect = useLive((s) => s.tagDetect);
  const alerts = useLive((s) => s.alerts);
  const fast = useLive((s) => s.fast);
  const renderDelay = useLive((s) => s.renderDelayMs);
  const missionsVersion = useLive((s) => s.missionsVersion);
  const [panelOpen, setPanelOpen] = useState(true);
  const [selTag, setSelTag] = useState<number | null>(null);
  const [mission, setMission] = useState<Mission | null>(null);

  // tuyến bay dự kiến còn lại: nhiệm vụ đang ready/running
  useEffect(() => {
    get<Mission[]>("/api/missions?state=ready,running").then(async (list) => {
      const m = list[0];
      setMission(m ? await get<Mission>(`/api/missions/${m.id}`) : null);
    }).catch(() => setMission(null));
  }, [missionsVersion]);

  const areaAlert = alerts.some((a) => a.code === "AREA_OUTSIDE" || a.code === "AREA_NEAR_EDGE" || a.code === "NOFLY_INSIDE");
  const wireMatches = mission && shadow && (mission.id & 0xffff) === shadow.mission_id;

  return (
    <div className="op-layout">
      <aside className="op-left panel">
        <h3>Tag</h3>
        <ul className="tag-list">
          {tags.filter((t) => t.enabled).map((t) => (
            <li key={t.tag_id} className={selTag === t.tag_id ? "sel" : ""} onClick={() => setSelTag(t.tag_id)}>
              <i style={{ background: KIND_COLOR[t.kind] }} />
              <b>{t.label}</b> <span className="muted">#{t.tag_id}</span>
              <small>{t.pos_n_m.toFixed(1)}, {t.pos_e_m.toFixed(1)}</small>
            </li>
          ))}
        </ul>
        {mission && <div className="muted small">Tuyến: {mission.plan_name} ({mission.state})</div>}
      </aside>
      <main className="op-center">
        <Scene buffer={liveBuffer} clock={liveClock} delayMs={renderDelay} tags={tags} areas={areas}
          plan={mission?.waypoints ?? null} wpIndex={wireMatches ? shadow!.wp_index : undefined}
          expectedTag={shadow?.expected_tag ?? null} tagDetect={tagDetect} areaAlert={areaAlert}
          failsafe={!!fast?.failsafe_active} highlightTags={selTag !== null ? new Set([selTag]) : undefined}
          onTagClick={(t) => setSelTag(t.tag_id)} />
        <Hud />
      </main>
      <aside className={`op-right${panelOpen ? "" : " collapsed"}`}>
        <button className="collapse" onClick={() => setPanelOpen(!panelOpen)}>{panelOpen ? "›" : "‹"}</button>
        {panelOpen && <>
          <ManualControl tags={tags.filter((t) => t.enabled)} gotoTag={selTag} />
          <TelemetryPanel />
        </>}
      </aside>
    </div>
  );
}
