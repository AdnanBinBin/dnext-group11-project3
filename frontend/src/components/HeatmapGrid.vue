<script setup>
import { computed } from "vue";
import { country, period } from "../labels.js";

const props = defineProps({
  cells: { type: Array, required: true },
  loading: Boolean,
  selected: { type: Object, default: null },
  threshold: { type: Number, default: 35 },
});
const emit = defineEmits(["select"]);

const periods = computed(() => [...new Set(props.cells.map((c) => c.period))].sort());
// Une ligne par pays client ; une case « empty » quand il n'y a pas eu d'échange
const rows = computed(() => {
  const byKey = new Map(props.cells.map((c) => [`${c.partner}|${c.period}`, c]));
  return [...new Set(props.cells.map((c) => c.partner))].map((partner) => ({
    partner,
    cells: periods.value.map((p) => byKey.get(`${partner}|${p}`) || { partner, period: p, status: "empty" }),
  }));
});

const isSelected = (cell) => props.selected
  && props.selected.partner === cell.partner && props.selected.period === cell.period;

function describe(cell) {
  const where = `${country(cell.partner)}, ${period(cell.period)}`;
  if (cell.status === "flagged") return `${where} : ${cell.anomaly_count} mois avec écart`;
  if (cell.status === "consistent") return `${where} : déclarations concordantes`;
  if (cell.status === "empty") return `${where} : pas d'échange`;
  return `${where} : déclaration du pays partenaire manquante`;
}
</script>

<template>
  <section class="zone">
    <ul class="key">
      <li><span class="swatch flagged"></span>Écart à vérifier (nombre de mois)</li>
      <li><span class="swatch consistent"></span>Déclarations concordantes</li>
      <li><span class="swatch no_match"></span>Déclaration du pays partenaire manquante</li>
      <li><span class="swatch empty"></span>Pas d'échange</li>
    </ul>

    <div class="scroll" :class="{ loading }">
      <table v-if="cells.length" class="grid">
        <thead>
          <tr><th></th><th v-for="p in periods" :key="p" scope="col">{{ period(p) }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.partner">
            <th scope="row">{{ country(row.partner) }}</th>
            <td v-for="cell in row.cells" :key="cell.period">
              <button
                v-if="cell.status === 'flagged'" type="button" class="cell flagged"
                :aria-pressed="isSelected(cell)" :aria-label="describe(cell)" :title="describe(cell)"
                @click="emit('select', cell)"
              >{{ cell.anomaly_count }}</button>
              <div v-else class="cell" :class="cell.status" :title="describe(cell)"></div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <p class="hint">
      Un écart est signalé quand le prix déclaré à l'import (CIF) est inférieur au prix déclaré à l'export (FOB),
      ou le dépasse de plus de {{ threshold }} %. Prix comparés par tonne, mois par mois.
    </p>
  </section>
</template>

<style scoped>
.zone { min-width: 0; min-height: 0; overflow: auto; padding: 18px clamp(16px, 3vw, 32px); }

.key { display: flex; flex-wrap: wrap; gap: 6px 20px; margin: 0 0 14px; padding: 0; list-style: none;
       font-size: 13px; color: var(--ink-soft); }
.key li { display: flex; align-items: center; gap: 7px; }
.swatch { width: 18px; height: 18px; border-radius: 3px; flex: none; }
.swatch.flagged { background: var(--signal); }
.swatch.consistent { background: var(--mist); }
.swatch.no_match { border: 1.5px dashed var(--dash); }
.swatch.empty { background: var(--paper); }

.scroll { overflow-x: auto; transition: opacity .15s; }
.scroll.loading { opacity: .45; pointer-events: none; }

table.grid { border-collapse: separate; border-spacing: 4px; }
.grid th { font-weight: 500; font-size: 13px; color: var(--ink-soft); padding: 2px 6px; white-space: nowrap; }
.grid thead th { text-align: center; }
.grid tbody th {
  text-align: left; color: var(--ink); font-weight: 600; padding-right: 12px;
  position: sticky; left: 0; z-index: 1; background: var(--sheet);
}
.grid td { padding: 0; }

.cell {
  display: grid; place-items: center; width: 100%; min-width: 58px; height: 40px;
  border-radius: 4px; border: 0; padding: 0; font-size: 15px;
}
.cell.flagged { background: var(--signal); color: #fff; font-weight: 700; cursor: pointer; }
.cell.flagged:hover { background: var(--signal-dark); }
.cell.flagged[aria-pressed="true"] { box-shadow: 0 0 0 3px var(--sheet), 0 0 0 6px var(--accent); }
.cell.consistent { background: var(--mist); }
.cell.consistent::after { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--ok); }
.cell.no_match { background: transparent; border: 1.5px dashed var(--dash); }
.cell.empty { background: var(--paper); }

.hint { font-size: 13px; color: var(--ink-soft); margin: 14px 0 0; max-width: 70ch; }
</style>
