// The application under test. One binary, one image, one handler — the two arms
// of this experiment differ only in container lifecycle, never in code.
//
// It also reports its own resource consumption, because that is the primary
// result of this experiment: cost here is MODELLED from measured resources
// (CPU-seconds, peak RSS, request count, wall time), never billed.
//
// CPU-seconds come from getrusage(RUSAGE_SELF), which counts user+system time
// for this process. That excludes the container runtime's own work (containerd,
// image unpack, the network namespace), so it UNDERSTATES what a real platform
// meters. The understatement applies equally to both arms and is stated as a
// threat to validity in the README rather than papered over.
package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strconv"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/dhiazfathra/serverless-vs-k8s-cost/internal/work"
)

// processStart is taken as early as possible so /stats can report how long this
// process has been alive. For the scale-to-zero arm that is the lifetime of one
// burst's instance; for always-on it is the lifetime of the whole cell.
var processStart = time.Now()

type counters struct {
	requests    atomic.Uint64
	roundsTotal atomic.Uint64
}

// A baseline snapshot. /reset takes one; /stats reports the delta from it, so a
// long-lived always-on process can be measured per burst without restarting.
type baseline struct {
	at     time.Time
	utime  float64
	stime  float64
	reqs   uint64
	rounds uint64
}

func rusage() (utime, stime float64, maxRSSBytes uint64) {
	var ru syscall.Rusage
	if err := syscall.Getrusage(syscall.RUSAGE_SELF, &ru); err != nil {
		return 0, 0, 0
	}
	tv := func(t syscall.Timeval) float64 {
		return float64(t.Sec) + float64(t.Usec)/1e6
	}
	// ru_maxrss is kilobytes on Linux and bytes on Darwin. The image is Linux;
	// the constant is named so the assumption is visible rather than implied.
	const linuxMaxRSSUnitBytes = 1024
	return tv(ru.Utime), tv(ru.Stime), uint64(ru.Maxrss) * linuxMaxRSSUnitBytes
}

func main() {
	rounds := envInt("WORK_ROUNDS", 200)
	listen := envStr("LISTEN", ":8080")

	var c counters
	var base atomic.Pointer[baseline]
	snapshot := func() *baseline {
		u, s, _ := rusage()
		return &baseline{at: time.Now(), utime: u, stime: s,
			reqs: c.requests.Load(), rounds: c.roundsTotal.Load()}
	}
	base.Store(snapshot())

	mux := http.NewServeMux()

	// The measured endpoint. Response bytes are a pure function of id and
	// WORK_ROUNDS, so bench/parity_test.go can recompute them independently.
	mux.HandleFunc("/work", func(w http.ResponseWriter, r *http.Request) {
		id, err := strconv.ParseUint(r.URL.Query().Get("id"), 10, 64)
		if err != nil {
			http.Error(w, "bad id", http.StatusBadRequest)
			return
		}
		digest := work.Do(id, rounds)
		c.requests.Add(1)
		c.roundsTotal.Add(uint64(rounds))
		w.Header().Set("Content-Type", "application/json")
		// Marshalled by hand so the byte layout is fixed by this line and not by
		// map ordering. Both arms serve the same bytes for the same id.
		_, _ = w.Write([]byte(`{"id":` + strconv.FormatUint(id, 10) +
			`,"rounds":` + strconv.Itoa(rounds) + `,"digest":"` + digest + `"}`))
	})

	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok\n"))
	})

	mux.HandleFunc("/reset", func(w http.ResponseWriter, _ *http.Request) {
		base.Store(snapshot())
		w.WriteHeader(http.StatusNoContent)
	})

	// The resource meter. Everything here is measured; nothing is priced.
	mux.HandleFunc("/stats", func(w http.ResponseWriter, _ *http.Request) {
		u, s, rss := rusage()
		b := base.Load()
		reqs := c.requests.Load() - b.reqs
		out := map[string]any{
			"work_rounds":      rounds,
			"requests":         reqs,
			"rounds_total":     c.roundsTotal.Load() - b.rounds,
			"utime_s":          u - b.utime,
			"stime_s":          s - b.stime,
			"cpu_s":            (u - b.utime) + (s - b.stime),
			"cpu_s_cumulative": u + s,
			"peak_rss_bytes":   rss,
			"since_reset_s":    time.Since(b.at).Seconds(),
			"process_uptime_s": time.Since(processStart).Seconds(),
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(out)
	})

	srv := &http.Server{Addr: listen, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	log.Printf("listening on %s, work_rounds=%d", listen, rounds)
	log.Fatal(srv.ListenAndServe())
}

func envStr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func envInt(k string, def int) int {
	if v := os.Getenv(k); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			log.Fatalf("%s=%q is not an integer", k, v)
		}
		return n
	}
	return def
}
