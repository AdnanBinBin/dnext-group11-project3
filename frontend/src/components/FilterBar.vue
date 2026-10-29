<script setup>
import { capitalize, country, FREQUENCIES, product } from "../labels.js";

defineProps({
  options: { type: Object, required: true },
  filters: { type: Object, required: true },
});
const emit = defineEmits(["change"]);
</script>

<template>
  <section class="filters" aria-label="Filtres">
    <div class="field">
      <label class="label" for="exporter">Pays exportateur</label>
      <div class="select">
        <select id="exporter" :value="filters.exporter" @change="emit('change', 'exporter', $event.target.value)">
          <option v-for="code in options.exporters" :key="code" :value="code">{{ country(code) }}</option>
        </select>
      </div>
    </div>

    <div class="field wide">
      <span id="product-label" class="label">Produit</span>
      <div class="chips" role="radiogroup" aria-labelledby="product-label">
        <button
          v-for="p in options.products" :key="p.hs4" type="button" role="radio" class="chip"
          :aria-checked="filters.product === p.hs4" @click="emit('change', 'product', p.hs4)"
        >{{ capitalize(product(p.hs4, p.name)) }}</button>
      </div>
    </div>

    <div class="field">
      <span id="year-label" class="label">Année</span>
      <div class="chips" role="radiogroup" aria-labelledby="year-label">
        <button
          type="button" role="radio" class="chip" :aria-checked="filters.year === ''"
          @click="emit('change', 'year', '')"
        >Toutes</button>
        <button
          v-for="y in options.years" :key="y" type="button" role="radio" class="chip"
          :aria-checked="filters.year === y" @click="emit('change', 'year', y)"
        >{{ y }}</button>
      </div>
    </div>

    <div class="field">
      <span id="frequency-label" class="label">Une case par</span>
      <div class="segmented" role="radiogroup" aria-labelledby="frequency-label">
        <button
          v-for="f in FREQUENCIES" :key="f.value" type="button" role="radio"
          :aria-checked="filters.frequency === f.value" @click="emit('change', 'frequency', f.value)"
        >{{ f.label }}</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.filters {
  display: flex; flex-wrap: wrap; gap: 14px 28px; align-items: flex-start;
  padding: 16px clamp(16px, 3vw, 32px) 18px; background: var(--sheet);
}
.field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.field.wide { flex: 1 1 360px; }
.label { font-size: 13px; color: var(--ink-soft); }

/* Liste déroulante : un champ, pas un soulignement */
.select { position: relative; }
.select::after {
  content: ""; position: absolute; right: 12px; top: 50%; width: 7px; height: 7px; margin-top: -5px;
  border-right: 2px solid var(--ink); border-bottom: 2px solid var(--ink); transform: rotate(45deg);
  pointer-events: none;
}
select {
  appearance: none; min-width: 190px; height: 36px; padding: 0 34px 0 12px; cursor: pointer;
  font-weight: 600; background: var(--paper); border: 1px solid var(--line); border-radius: 6px;
}
select:hover { border-color: var(--ink-soft); }

/* Choix uniques : une rangée de pastilles, la sélection est pleine */
.chips { display: flex; gap: 6px; overflow-x: auto; scrollbar-width: thin; padding-bottom: 2px; }
.chip {
  flex: none; height: 36px; padding: 0 13px; cursor: pointer; white-space: nowrap;
  background: var(--sheet); border: 1px solid var(--line); border-radius: 18px;
}
.chip:hover { border-color: var(--ink-soft); }
.chip[aria-checked="true"] { background: var(--navy); border-color: var(--navy); color: #fff; font-weight: 600; }

/* Découpage de la grille : contrôle segmenté */
.segmented { display: inline-flex; background: var(--paper); border: 1px solid var(--line); border-radius: 6px; padding: 2px; }
.segmented button {
  height: 30px; padding: 0 14px; border: 0; border-radius: 4px; background: transparent; cursor: pointer;
}
.segmented button[aria-checked="true"] { background: var(--sheet); box-shadow: 0 0 0 1px var(--line); font-weight: 600; }

@media (max-width: 700px) {
  .field, .field.wide { flex: 1 1 100%; }
  select { width: 100%; }
}
</style>
