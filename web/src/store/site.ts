import { create } from "zustand";
import { get } from "../lib/api";
import type { Area, Issue, MapStatus, Site, Tag } from "../lib/types";

interface SiteState {
  site: Site | null;
  tags: Tag[];
  areas: Area[];
  issues: Issue[];
  map: MapStatus | null;
  loadedVersion: number;
  load: (siteId?: number) => Promise<void>;
}

export const useSite = create<SiteState>((set, getState) => ({
  site: null, tags: [], areas: [], issues: [], map: null, loadedVersion: -1,
  load: async (siteId) => {
    const id = siteId ?? getState().site?.id ?? 1;
    const d = await get<{ site: Site; tags: Tag[]; areas: Area[]; issues: Issue[]; map: MapStatus | null }>(`/api/sites/${id}/design`);
    set({ site: d.site, tags: d.tags, areas: d.areas, issues: d.issues, map: d.map });
  },
}));
