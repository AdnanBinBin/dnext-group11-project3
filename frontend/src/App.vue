<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { configured, get, UNAVAILABLE } from "./api.js";
import { country, product } from "./labels.js";
import FilterBar from "./components/FilterBar.vue";
import HeatmapGrid from "./components/HeatmapGrid.vue";
import AnomalyPanel from "./components/AnomalyPanel.vue";
import logo from "./assets/logo-dnext.svg";

// Options proposées par l'API, et filtres choisis
const options = reactive({ exporters: [], products: [], years: [], threshold: 35 });
const filters = reactive({ exporter: "", product: "", year: "", frequency: "yearly" });

// Grille affichée
const grid = reactive({ exporter: "", product: "", year: "", cells: [] });
const loading = ref(false);
const problem = ref("");
const selection = ref(null);    // { partner, period, count }
let lastRequest = 0;

onMounted(async () => {
  if (!configured) { problem.value = "Adresse du service non configurée."; return; }
  try {
    const f = await get("/api/v1/filters");
    options.exporters = [...f.exporters].sort((a, b) => country(a).localeCompare(country(b), "fr"));
    options.products = f.products;
    options.threshold = f.anomaly_threshold_pct || options.threshold;
    Object.assign(filters, { exporter: options.exporters[0], product: f.products[0].hs4 });
  } catch {
    problem.value = UNAVAILABLE;
  }
});

// Chaque changement de filtre recharge la grille seule ; la grille précédente reste
// affichée (atténuée) jusqu'à l'arrivée de la nouvelle.
watch(filters, loadGrid);

async function loadGrid() {
  if (!filters.exporter || !filters.product) return;
  const request = ++lastRequest;
  const params = new URLSearchParams({
    exporter: filters.exporter, product: filters.product, frequency: filters.frequency,
  });
  if (filters.year) { params.set("start", `${filters.year}-01`); params.set("end", `${filters.year}-12`); }
  loading.value = true;
  try {
    const data = await get(`/api/v1/heatmap?${params}`);
    if (request !== lastRequest) return;   // une réponse plus récente est attendue
    Object.assign(grid, { exporter: data.exporter, product: data.product, year: filters.year, cells: data.cells });
    rememberYears(data.cells);
    selection.value = null;
    problem.value = "";
  } catch {
    if (request === lastRequest) problem.value = UNAVAILABLE;
  } finally {
    if (request === lastRequest) loading.value = false;
  }
}

// Les années proposées sont celles où l'on a vu des échanges
function rememberYears(cells) {
  const years = new Set([...options.years, ...cells.map((c) => c.period.slice(0, 4))]);
  options.years = [...years].sort().reverse();
}

const verdict = computed(() => {
  const cells = grid.cells;
  const flagged = cells.filter((c) => c.status === "flagged");
  const clients = new Set(cells.map((c) => c.partner)).size;
  const byPartner = {};
  for (const c of flagged) byPartner[c.partner] = (byPartner[c.partner] || 0) + c.anomaly_count;
  const worst = Object.entries(byPartner).sort((a, b) => b[1] - a[1])[0];
  return {
    count: flagged.length,
    total: cells.length,
    clients,
    // « Argentine » mais « États-Unis » : on évite les articles devant le nom du pays
    what: [product(grid.product), `exportateur ${country(grid.exporter)}`, grid.year].filter(Boolean).join(", "),
    worst: worst && { name: country(worst[0]), months: worst[1] },
  };
});

const panel = ref(null);
function select(cell) {
  selection.value = cell;
  // Sur petit écran, le panneau est sous la grille : on l'amène à l'écran
  if (window.matchMedia("(max-width: 1100px)").matches) {
    requestAnimationFrame(() => panel.value?.$el.scrollIntoView({ behavior: "smooth", block: "start" }));
  }
}
</script>

<template>
  <div class="app">
    <header class="top">
      <img :src="logo" alt="DNEXT" class="logo" width="103" height="32">
      <h1>Contrôle des déclarations export et import <span class="group">Group 11</span></h1>
    </header>

    <FilterBar :options="options" :filters="filters" @change="(key, value) => (filters[key] = value)" />

    <section class="verdict" :class="{ calm: grid.cells.length && !verdict.count }" aria-live="polite">
      <p v-if="problem" class="problem">{{ problem }}</p>
      <p v-else-if="!grid.cells.length" class="waiting">Recherche des écarts…</p>
      <template v-else>
        <strong>{{ verdict.count }}</strong>
        <p v-if="verdict.count">
          {{ verdict.count > 1 ? "périodes" : "période" }} à vérifier sur {{ verdict.total }}
          ({{ verdict.what }}, <b>{{ verdict.clients }} principaux pays clients</b>).
          <template v-if="verdict.worst">
            Pays le plus concerné : <b>{{ verdict.worst.name }}</b>, {{ verdict.worst.months }} mois avec écart.
          </template>
        </p>
        <p v-else>
          période à vérifier sur {{ verdict.total }} ({{ verdict.what }}, <b>{{ verdict.clients }} principaux
          pays clients</b>) : les déclarations concordent.
        </p>
      </template>
    </section>

    <main class="workspace">
      <HeatmapGrid
        :cells="grid.cells"
        :loading="loading"
        :selected="selection"
        :threshold="options.threshold"
        @select="select"
      />
      <AnomalyPanel
        ref="panel"
        :exporter="grid.exporter"
        :product="grid.product"
        :selection="selection"
      />
    </main>
  </div>
</template>

<style scoped>
/* Mise en page « application » : en-tête et filtres fixes, grille et panneau défilent chacun de leur côté */
.app { height: 100%; display: grid; grid-template-rows: auto auto auto minmax(0, 1fr); }

/* Bandeau aux couleurs DNEXT : le logo est prévu pour un fond sombre */
.top {
  display: flex; align-items: center; gap: 12px 20px; flex-wrap: wrap;
  padding: 12px clamp(16px, 3vw, 32px); background: var(--navy); color: #fff;
}
.logo { display: block; height: 32px; width: auto; }
.top h1 {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 0;
  padding-left: 20px; border-left: 1px solid rgba(255, 255, 255, .25);
  font-size: 15px; font-weight: 500; color: rgba(255, 255, 255, .9);
}
.group {
  font-size: 13px; font-weight: 600; color: var(--accent);
  border: 1px solid var(--accent); border-radius: 12px; padding: 1px 10px;
}
@media (max-width: 600px) { .top h1 { padding-left: 0; border-left: 0; } }

.verdict {
  display: flex; gap: 16px; align-items: baseline; min-height: 74px;
  padding: 14px clamp(16px, 3vw, 32px); border-top: 1px solid var(--line);
}
.verdict strong {
  font-size: clamp(40px, 5vw, 54px); font-weight: 800; letter-spacing: -.03em; line-height: 1; color: var(--signal);
}
.verdict p { margin: 0; max-width: 72ch; color: var(--ink-soft); }
.verdict b { color: var(--ink); font-weight: 600; }
.verdict.calm strong { color: var(--ok); }

.workspace {
  display: grid; grid-template-columns: minmax(0, 1fr) minmax(360px, 460px);
  min-height: 0; border-top: 1px solid var(--line); background: var(--sheet);
}

@media (max-width: 1100px) {
  .app { height: auto; display: block; }
  .workspace { grid-template-columns: 1fr; }
}
</style>
