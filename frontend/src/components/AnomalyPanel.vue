<script setup>
import { computed, ref, watch } from "vue";
import { get, UNAVAILABLE } from "../api.js";
import { capitalize, country, monthRange, num, period, product, REASONS, signed } from "../labels.js";
import ReportView from "./ReportView.vue";

const props = defineProps({
  exporter: { type: String, default: "" },
  product: { type: String, default: "" },
  selection: { type: Object, default: null },   // case de la grille : { partner, period, anomaly_count }
});

const items = ref([]);
const loading = ref(false);
const problem = ref("");
const report = ref(null);   // mois pour lequel le rapport est affiché

// Nouvelle case choisie : on charge ses mois, sans toucher au reste de la page
watch(() => props.selection, async (cell) => {
  report.value = null;
  items.value = [];
  problem.value = "";
  if (!cell) return;
  const [start, end] = monthRange(cell.period);
  const params = new URLSearchParams({
    exporter: props.exporter, importer: cell.partner, product: props.product, start, end, limit: 50,
  });
  loading.value = true;
  try {
    const data = await get(`/api/v1/anomalies?${params}`);
    if (cell !== props.selection) return;
    items.value = [...data.items].sort((a, b) => a.year_month.localeCompare(b.year_month));
  } catch {
    if (cell === props.selection) problem.value = UNAVAILABLE;
  } finally {
    if (cell === props.selection) loading.value = false;
  }
});

const when = computed(() => {
  const p = props.selection?.period || "";
  if (/^\d{4}$/.test(p)) return `en ${p}`;
  return /Q/.test(p) ? `au ${period(p, true)}` : `en ${period(p, true)}`;
});

const width = (value, a) => `${(value / (Math.max(a.fob_price || 0, a.cif_price || 0) || 1)) * 100}%`;
</script>

<template>
  <aside class="panel" aria-live="polite">
    <p v-if="!selection" class="empty-state">
      Sélectionnez une <b>case rouge</b> pour voir les mois concernés et obtenir un rapport explicatif à partager.
    </p>

    <template v-else>
      <header class="head">
        <button v-if="report" type="button" class="quiet back" @click="report = null">Retour à la liste des mois</button>
        <h2>{{ country(exporter) }} vers {{ country(selection.partner) }}</h2>
        <p class="sub">
          {{ capitalize(product(props.product)) }}, {{ when }}.
          {{ selection.anomaly_count }} mois avec écart.
        </p>
      </header>

      <div class="body">
        <ReportView v-if="report" :key="report.anomaly_id" :anomaly="report" />
        <p v-else-if="loading" class="waiting">Chargement des mois concernés…</p>
        <p v-else-if="problem" class="problem">{{ problem }}</p>
        <ul v-else class="shipments">
          <li v-for="a in items" :key="a.anomaly_id" class="shipment">
            <div class="ship-head">
              <span class="month">{{ capitalize(period(a.year_month, true)) }}</span>
              <span class="gap" title="Écart entre prix import et prix export">{{ signed(a.gap_pct) }}</span>
            </div>
            <p class="why">{{ REASONS[a.anomaly_reason] }}</p>
            <div class="bars">
              <span>Export (FOB)</span><span class="bar" :style="{ width: width(a.fob_price, a) }"></span>
              <span class="val">{{ num(a.fob_price) }} USD/t</span>
              <span>Import (CIF)</span><span class="bar cif" :style="{ width: width(a.cif_price, a) }"></span>
              <span class="val">{{ num(a.cif_price) }} USD/t</span>
            </div>
            <button type="button" class="go" @click="report = a">Obtenir le rapport explicatif</button>
          </li>
        </ul>
      </div>
    </template>
  </aside>
</template>

<style scoped>
/* Le panneau a sa propre hauteur : seule la liste défile, pas la page */
.panel {
  display: flex; flex-direction: column; min-height: 0; overflow: hidden;
  border-left: 1px solid var(--line); background: #fbfcfc;
}
.empty-state { margin: 0; padding: 24px clamp(16px, 3vw, 28px); color: var(--ink-soft); max-width: 40ch; }
.empty-state b { color: var(--signal); font-weight: 600; }

.head { flex: none; padding: 20px clamp(16px, 3vw, 28px) 14px; border-bottom: 1px solid var(--line); }
.head h2 { margin: 0; font-size: 21px; font-weight: 700; letter-spacing: -.01em; line-height: 1.25; }
.sub { margin: 4px 0 0; color: var(--ink-soft); font-size: 14px; }
.back { display: block; margin-bottom: 10px; }

.body { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 4px clamp(16px, 3vw, 28px) 24px; }
.body > .waiting, .body > .problem { margin-top: 16px; }

.shipments { list-style: none; margin: 0; padding: 0; }
.shipment { padding: 16px 0; border-bottom: 1px solid var(--line); }
.ship-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.month { font-weight: 700; }
.gap { font-weight: 800; color: var(--signal); font-size: 18px; }
.why { font-size: 13px; color: var(--ink-soft); margin: 2px 0 10px; }
.bars { display: grid; grid-template-columns: 96px 1fr auto; gap: 4px 10px; align-items: center; font-size: 13px; margin-bottom: 12px; }
.bar { height: 10px; border-radius: 2px; background: var(--ink); }
.bar.cif { background: var(--signal); }
.val { text-align: right; white-space: nowrap; }

@media (max-width: 1100px) {
  .panel { border-left: 0; border-top: 1px solid var(--line); max-height: 85vh; }
}
</style>
