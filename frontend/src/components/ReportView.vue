<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { get, post, UNAVAILABLE } from "../api.js";
import { capitalize, period } from "../labels.js";

const props = defineProps({ anomaly: { type: Object, required: true } });

const state = ref("PENDING");     // PENDING | RUNNING | COMPLETED | FAILED | ERROR
const report = ref(null);
const reused = ref(false);
const seconds = ref(0);
let run = 0;                       // invalide les attentes d'une demande précédente
let timer = null;

const STEPS = ["Demande reçue", "Analyse des déclarations", "Rapport prêt"];
const reached = () => ({ PENDING: 0, RUNNING: 1, COMPLETED: 3 })[state.value] ?? 0;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function ask(force) {
  const current = ++run;
  state.value = "PENDING";
  report.value = null;
  const started = Date.now();
  clearInterval(timer);
  timer = setInterval(() => { seconds.value = Math.round((Date.now() - started) / 1000); }, 1000);
  try {
    let data = await post("/api/v1/reports", { anomaly_id: props.anomaly.anomaly_id, force });
    reused.value = Boolean(data.reused);
    while (current === run && !["COMPLETED", "FAILED"].includes(data.status)) {
      state.value = data.status;
      await sleep(3000);
      data = await get(`/api/v1/reports/${data.report_id}`, { fresh: true });
    }
    if (current !== run) return;
    state.value = data.status;
    report.value = data;
  } catch {
    if (current === run) state.value = "ERROR";
  } finally {
    if (current === run) clearInterval(timer);
  }
}

onMounted(() => ask(false));
onBeforeUnmount(() => { run++; clearInterval(timer); });
</script>

<template>
  <section class="report">
    <h3>Rapport sur {{ period(anomaly.year_month, true) }}</h3>

    <ol v-if="state !== 'FAILED' && state !== 'ERROR'" class="progress">
      <li v-for="(label, i) in STEPS" :key="label" :class="{ done: i < reached(), now: i === reached() }">{{ label }}</li>
    </ol>

    <template v-if="state === 'COMPLETED'">
      <p class="title">{{ report.title }}</p>
      <p class="meta">
        {{ reused ? "Ce rapport avait déjà été rédigé." : "Rapport rédigé à l'instant." }}
        Le lien de téléchargement reste valable une heure.
      </p>
      <div class="actions">
        <a class="go" :href="report.pdf_url" target="_blank" rel="noopener">Télécharger le PDF</a>
        <button type="button" class="quiet" @click="ask(true)">Rédiger une nouvelle version</button>
      </div>
      <iframe :src="report.pdf_url" :title="`Aperçu du rapport : ${capitalize(period(anomaly.year_month, true))}`"></iframe>
    </template>

    <template v-else-if="state === 'FAILED'">
      <p class="problem">
        Le rapport n'a pas pu être rédigé. Relancez la demande ; si l'échec se répète, réessayez plus tard.
      </p>
      <button type="button" class="go" @click="ask(true)">Relancer la demande</button>
    </template>

    <p v-else-if="state === 'ERROR'" class="problem">{{ UNAVAILABLE }}</p>

    <p v-else class="meta">Rédaction en cours, environ une minute ({{ seconds }} s).</p>
  </section>
</template>

<style scoped>
.report { padding-top: 18px; }
h3 { margin: 0 0 12px; font-size: 17px; }

.progress { list-style: none; margin: 0 0 14px; padding: 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
.progress li { font-size: 12px; color: var(--ink-soft); padding-top: 8px; border-top: 4px solid var(--line); }
.progress li.done { color: var(--ink); border-top-color: var(--ink); }
.progress li.now { color: var(--ink); border-top-color: var(--accent); animation: breathe 1.4s ease-in-out infinite; }
@keyframes breathe { 50% { border-top-color: #b4f0e7; } }

.title { font-weight: 600; margin: 0 0 8px; }
.meta { font-size: 13px; color: var(--ink-soft); margin: 0 0 14px; }
.actions { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
iframe { display: block; width: 100%; height: max(480px, 68vh); border: 1px solid var(--line); border-radius: 4px; margin-top: 16px; background: #fff; }
</style>
