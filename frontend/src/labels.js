// Libellés métier : noms de pays et de produits, périodes, montants.

const A3_TO_A2 = Object.fromEntries(("ABW:AW AFG:AF AGO:AO AIA:AI ALB:AL AND:AD ARE:AE ARG:AR ARM:AM ATF:TF ATG:AG " +
  "AUS:AU AUT:AT AZE:AZ BDI:BI BEL:BE BEN:BJ BFA:BF BGD:BD BGR:BG BHR:BH BHS:BS BIH:BA BLM:BL BLR:BY BLZ:BZ " +
  "BMU:BM BOL:BO BRA:BR BRB:BB BRN:BN BTN:BT BWA:BW CAF:CF CAN:CA CHE:CH CHL:CL CHN:CN CIV:CI CMR:CM COD:CD " +
  "COG:CG COK:CK COL:CO COM:KM CPV:CV CRI:CR CUB:CU CUW:CW CXR:CX CYM:KY CYP:CY CZE:CZ DEU:DE DJI:DJ DMA:DM " +
  "DNK:DK DOM:DO DZA:DZ ECU:EC EGY:EG ERI:ER ESP:ES EST:EE ETH:ET FIN:FI FJI:FJ FRA:FR FRO:FO FSM:FM GAB:GA " +
  "GBR:GB GEO:GE GHA:GH GIB:GI GIN:GN GMB:GM GNB:GW GNQ:GQ GRC:GR GRD:GD GRL:GL GTM:GT GUY:GY HKG:HK HND:HN " +
  "HRV:HR HTI:HT HUN:HU IDN:ID IND:IN IOT:IO IRL:IE IRN:IR IRQ:IQ ISL:IS ISR:IL ITA:IT JAM:JM JOR:JO JPN:JP " +
  "KAZ:KZ KEN:KE KGZ:KG KHM:KH KIR:KI KNA:KN KOR:KR KWT:KW LAO:LA LBN:LB LBR:LR LBY:LY LCA:LC LKA:LK LSO:LS " +
  "LTU:LT LUX:LU LVA:LV MAC:MO MAF:MF MAR:MA MDA:MD MDG:MG MDV:MV MEX:MX MHL:MH MKD:MK MLI:ML MLT:MT MMR:MM " +
  "MNE:ME MNG:MN MOZ:MZ MRT:MR MSR:MS MUS:MU MWI:MW MYS:MY NAM:NA NCL:NC NER:NE NFK:NF NGA:NG NIC:NI NLD:NL " +
  "NOR:NO NPL:NP NRU:NR NZL:NZ OMN:OM PAK:PK PAN:PA PCN:PN PER:PE PHL:PH PLW:PW PNG:PG POL:PL PRK:KP PRT:PT " +
  "PRY:PY PSE:PS PYF:PF QAT:QA ROU:RO RUS:RU RWA:RW SAU:SA SDN:SD SEN:SN SGP:SG SLB:SB SLE:SL SLV:SV SMR:SM " +
  "SOM:SO SPM:PM SRB:RS SSD:SS STP:ST SUR:SR SVK:SK SVN:SI SWE:SE SWZ:SZ SYC:SC SYR:SY TCA:TC TCD:TD TGO:TG " +
  "THA:TH TJK:TJ TKL:TK TKM:TM TLS:TL TON:TO TTO:TT TUN:TN TUR:TR TUV:TV TZA:TZ UGA:UG UKR:UA UMI:UM URY:UY " +
  "USA:US UZB:UZ VCT:VC VEN:VE VGB:VG VNM:VN VUT:VU WLF:WF WSM:WS YEM:YE ZAF:ZA ZMB:ZM ZWE:ZW")
  .split(" ").map((pair) => pair.split(":")));
const SPECIAL = { EUR_OTHERS: "Autres pays d'Europe", OTHER_ASIA: "Autres pays d'Asie", _X_: "Destination non précisée" };
const regionNames = new Intl.DisplayNames(["fr"], { type: "region" });

export const country = (code) => SPECIAL[code] || (A3_TO_A2[code] ? regionNames.of(A3_TO_A2[code]) : code);

const PRODUCTS = { "1001": "blé", "1003": "orge", "1005": "maïs", "1201": "soja", "1205": "colza",
                   "1206": "graines de tournesol", "2304": "tourteaux de soja" };
export const product = (hs4, fallback) => PRODUCTS[hs4] || (fallback || hs4).toLowerCase();
export const capitalize = (text) => text.charAt(0).toUpperCase() + text.slice(1);

export const FREQUENCIES = [
  { value: "yearly", label: "Année" },
  { value: "quarterly", label: "Trimestre" },
  { value: "monthly", label: "Mois" },
];

const MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."];
const LONG_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
                     "septembre", "octobre", "novembre", "décembre"];

// "2021" | "2021-Q3" | "2021-10"  ->  "2021" | "T3 2021" | "oct. 2021"
export function period(p, long = false) {
  const q = p.match(/^(\d{4})-Q([1-4])$/);
  if (q) return long ? `${q[2] === "1" ? "1er" : `${q[2]}e`} trimestre ${q[1]}` : `T${q[2]} ${q[1]}`;
  const m = p.match(/^(\d{4})-(\d{2})$/);
  if (m) return `${(long ? LONG_MONTHS : MONTHS)[Number(m[2]) - 1]} ${m[1]}`;
  return p;
}

// Période affichée -> intervalle de mois attendu par l'API
export function monthRange(p) {
  if (/^\d{4}$/.test(p)) return [`${p}-01`, `${p}-12`];
  const q = p.match(/^(\d{4})-Q([1-4])$/);
  if (q) {
    const first = (Number(q[2]) - 1) * 3 + 1;
    const mm = (m) => String(m).padStart(2, "0");
    return [`${q[1]}-${mm(first)}`, `${q[1]}-${mm(first + 2)}`];
  }
  return [p, p];
}

export const num = (x, d = 0) => x == null ? "–"
  : Number(x).toLocaleString("fr-CH", { maximumFractionDigits: d, minimumFractionDigits: d });
export const signed = (x) => x == null ? "–" : `${x >= 0 ? "+" : "−"}${num(Math.abs(x), 0)} %`;

export const REASONS = {
  CIF_BELOW_FOB: "Le prix déclaré à l'import est inférieur au prix à l'export, alors qu'il devrait inclure le fret et l'assurance.",
  GAP_ABOVE_35: "Le prix déclaré à l'import dépasse le prix à l'export de plus que ne l'expliquent le fret et l'assurance.",
};
