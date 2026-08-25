/* global __ENV */
import http from "k6/http";
import exec from "k6/execution";
import { Counter } from "k6/metrics";

// One burst of load, open loop. The harness invokes this once per burst; the
// arms differ only in what the harness does to the container between bursts.
//
// There is no warm-up phase, deliberately. A warm-up would hide exactly the
// thing the scale-to-zero arm exists to measure — the cost of starting cold —
// so the cold start is measured OUTSIDE k6 (by the harness, as the time from
// `docker compose up` to the first successful response) and reported as its own
// distribution. What k6 measures here is warm latency only, and the two are
// never averaged together.

const RATE = Number(__ENV.RATE || 2000);
const DUR = __ENV.DUR || "5s";
const VUS = Number(__ENV.VUS || 64);
const MAXVUS = Number(__ENV.MAXVUS || 1024);
// The id space. Ids are walked in a fixed order derived from the iteration
// index, so every burst of every arm issues the byte-identical request
// sequence. SEED is recorded in the output; it offsets the walk and is fixed
// across the sweep so the arms are comparable.
const IDS = Number(__ENV.IDS || 10000);
const SEED = Number(__ENV.SEED || 20260825);
const BASE = `http://app:8080/work`;

const divergent = new Counter("divergent_bodies");

export const options = {
  discardResponseBodies: false,
  summaryTrendStats: ["avg", "min", "med", "p(95)", "p(99)", "max"],
  // Declaring the thresholds is what makes these sub-metrics exist in
  // handleSummary. None of them is meant to fail: the gates live in
  // scripts/gate.py, which can refuse a cell and delete its file.
  thresholds: {
    http_req_duration: ["max>=0"],
    http_reqs: ["count>=0"],
    http_req_failed: ["rate>=0"],
    dropped_iterations: ["count>=0"],
    data_received: ["count>=0"],
  },
  scenarios: {
    burst: {
      executor: "constant-arrival-rate",
      rate: RATE,
      timeUnit: "1s",
      duration: DUR,
      preAllocatedVUs: VUS,
      maxVUs: MAXVUS,
    },
  },
};

// Response bodies are a fixed scaffold plus a 64-character digest plus the id's
// own decimal digits, so the length legitimately varies with the id -- ids 0 and
// 1999 differ by three bytes. A first attempt compared every body against the
// first body's length and reported 10 993 "divergent" bodies out of 20 000 on a
// cell where nothing was wrong at all. The check now subtracts the id's digit
// count, so it still fires on a genuinely truncated or substituted body and no
// longer fires on arithmetic.
let scaffoldLen = -1;

export default function () {
  const id = (exec.scenario.iterationInTest + SEED) % IDS;
  const res = http.get(`${BASE}?id=${id}`);
  const digits = String(id).length;
  const n = res.body ? res.body.length : 0;
  if (res.status !== 200) return;
  if (scaffoldLen < 0) scaffoldLen = n - digits;
  if (n !== scaffoldLen + digits) divergent.add(1);
}

export function handleSummary(data) {
  const m = data.metrics.http_req_duration.values;
  const reqs = data.metrics.http_reqs.values;
  const failed = data.metrics.http_req_failed;
  const dropped = data.metrics.dropped_iterations;
  const recv = data.metrics.data_received;
  const secs = durSeconds(DUR);
  return {
    stdout:
      JSON.stringify(
        {
          rate_offered: RATE,
          duration: DUR,
          duration_s: secs,
          // Declared from the sweep parameters, NOT counted from what arrived.
          // A gate that derives its expectation from the run it is grading
          // cannot fail.
          offered_total: Math.round(RATE * secs),
          vus: VUS,
          max_vus: MAXVUS,
          ids: IDS,
          seed: SEED,
          reqs: reqs.count,
          rps_achieved: reqs.count / secs,
          dropped: dropped ? dropped.values.count : 0,
          failed: failed ? failed.values.passes : 0,
          divergent_bodies: countOf(data, "divergent_bodies"),
          run_wall_s: data.state.testRunDurationMs / 1000,
          warm_p50_ms: m.med,
          warm_p95_ms: m["p(95)"],
          warm_p99_ms: m["p(99)"],
          warm_avg_ms: m.avg,
          warm_max_ms: m.max,
          recv_bytes: recv ? recv.values.count : 0,
        },
        null,
        2,
      ) + "\n",
  };
}

function countOf(data, name) {
  const s = data.metrics[name];
  return s ? s.values.count : 0;
}

function durSeconds(d) {
  const mm = /^(\d+(?:\.\d+)?)(ms|s|m)$/.exec(d);
  if (!mm) throw new Error(`unparseable duration ${d}`);
  const n = Number(mm[1]);
  return mm[2] === "ms" ? n / 1000 : mm[2] === "m" ? n * 60 : n;
}
