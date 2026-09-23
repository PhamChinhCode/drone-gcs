import { useEffect } from "react";
import { BrowserRouter, Navigate, NavLink, Route, Routes } from "react-router-dom";
import { AdminPage } from "../features/admin/AdminPage";
import { LinkDiagnostics } from "../features/admin/LinkDiagnostics";
import { AlertsPage } from "../features/alerts/AlertsPage";
import { HistoryPage } from "../features/history/HistoryPage";
import { DronePage } from "../features/drone/DronePage";
import { MissionPage } from "../features/mission/MissionPage";
import { Banners } from "../features/monitor/Banners";
import { EmergencyBar } from "../features/monitor/EmergencyBar";
import { OperationPage } from "../features/monitor/OperationPage";
import { SiteDesignPage } from "../features/sitedesign/SiteDesignPage";
import { TagsPage } from "../features/tags/TagsPage";
import { connectLive, disconnectLive } from "../lib/ws";
import { useAuth, useIsAdmin } from "../store/auth";
import { useLive } from "../store/live";
import { useSite } from "../store/site";
import { Login } from "./Login";

function Layout() {
  const user = useAuth((s) => s.user);
  const logout = useAuth((s) => s.logout);
  const admin = useIsAdmin();
  const alertCount = useLive((s) => s.alerts.filter((a) => !a.acked && a.severity >= 2).length);
  const siteVersion = useLive((s) => s.siteVersion);
  const loadSite = useSite((s) => s.load);

  useEffect(() => { connectLive(); return () => disconnectLive(); }, []);
  useEffect(() => { void loadSite(); }, [siteVersion, loadSite]);

  const nav: [string, string, boolean][] = [
    ["/", "Vận hành 3D", true], ["/drone", "Drone", true], ["/missions", "Nhiệm vụ", true], ["/history", "Lịch sử", true], ["/alerts", "Cảnh báo", true],
    ["/design", "Thiết kế khu vực", admin], ["/tags", "Tag", admin], ["/link", "Liên kết", admin], ["/admin", "Quản trị", admin],
  ];
  return (
    <div className="app">
      <header className="topbar">
        <b className="brand">GCS</b>
        <nav>
          {nav.filter(([, , show]) => show).map(([to, label]) => (
            <NavLink key={to} to={to} end={to === "/"}>{label}{to === "/alerts" && alertCount > 0 && <span className="badge">{alertCount}</span>}</NavLink>
          ))}
        </nav>
        <span className="user">{user?.username} ({user?.role}) <button className="link" onClick={() => { disconnectLive(); logout(); }}>Đăng xuất</button></span>
      </header>
      <Banners />
      <div className="content">
        <Routes>
          <Route path="/" element={<OperationPage />} />
          <Route path="/drone" element={<DronePage />} />
          <Route path="/missions" element={<MissionPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/design" element={admin ? <SiteDesignPage /> : <Navigate to="/" />} />
          <Route path="/tags" element={admin ? <TagsPage /> : <Navigate to="/" />} />
          <Route path="/link" element={admin ? <LinkDiagnostics /> : <Navigate to="/" />} />
          <Route path="/admin" element={admin ? <AdminPage /> : <Navigate to="/" />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </div>
      {/* thanh khẩn cấp: luôn hiển thị, không bao giờ bị che, không nằm trong tab (8.3) */}
      <EmergencyBar />
    </div>
  );
}

export function App() {
  const token = useAuth((s) => s.token);
  return <BrowserRouter>{token ? <Layout /> : <Login />}</BrowserRouter>;
}
