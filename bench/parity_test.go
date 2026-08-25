// The equivalence gate. No latency or cost number is recorded for a run whose
// arms have not been shown to do identical work.
//
// The two arms share one image and one service definition, so equal work is
// true by construction -- which is exactly the kind of claim that stops being
// true without anyone noticing. This test walks the whole id space the load
// generator uses, recomputes every response body in-process from
// internal/work, and asserts the server returned those exact bytes. It then
// asserts the server's own hash-round counter equals requests x WORK_ROUNDS, so
// a handler that quietly returned a cached or truncated answer cannot pass.
//
// scripts/run.sh runs it TWICE: once against a container that has just cold
// started (the scale-to-zero shape) and once against a container that has been
// up and serving (the always-on shape). Both must produce byte-identical
// bodies for the same ids.
package bench

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/dhiazfathra/serverless-vs-k8s-cost/internal/work"
)

func appURL() string {
	if v := os.Getenv("APP_URL"); v != "" {
		return v
	}
	return "http://127.0.0.1:18093"
}

func idSpace() int {
	if v := os.Getenv("IDS"); v != "" {
		n, err := strconv.Atoi(v)
		if err == nil {
			return n
		}
	}
	return 10000
}

func get(t *testing.T, url string) []byte {
	t.Helper()
	c := &http.Client{Timeout: 15 * time.Second}
	res, err := c.Get(url)
	if err != nil {
		t.Fatalf("GET %s: %v", url, err)
	}
	defer func() { _ = res.Body.Close() }()
	b, err := io.ReadAll(res.Body)
	if err != nil {
		t.Fatalf("read %s: %v", url, err)
	}
	if res.StatusCode != http.StatusOK {
		t.Fatalf("GET %s: status %d body %q", url, res.StatusCode, b)
	}
	return b
}

func stats(t *testing.T) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal(get(t, appURL()+"/stats"), &m); err != nil {
		t.Fatalf("decode /stats: %v", err)
	}
	return m
}

// TestEveryIDMatchesLocallyRecomputedTruth walks the entire id space the load
// generator draws from, not a sample of it.
func TestEveryIDMatchesLocallyRecomputedTruth(t *testing.T) {
	base := appURL()
	c := &http.Client{Timeout: 15 * time.Second}
	if _, err := c.Get(base + "/healthz"); err != nil {
		t.Skipf("no app at %s: %v", base, err)
	}

	rounds := int(stats(t)["work_rounds"].(float64))
	if rounds <= 0 {
		t.Fatalf("server reports work_rounds=%d", rounds)
	}

	res, err := c.Post(base+"/reset", "", nil)
	if err != nil {
		t.Fatalf("reset: %v", err)
	}
	_ = res.Body.Close()

	n := idSpace()
	for id := 0; id < n; id++ {
		want := fmt.Sprintf(`{"id":%d,"rounds":%d,"digest":"%s"}`,
			id, rounds, work.Do(uint64(id), rounds))
		got := string(get(t, fmt.Sprintf("%s/work?id=%d", base, id)))
		if got != want {
			t.Fatalf("id %d: server returned\n  %s\nlocally recomputed truth is\n  %s", id, got, want)
		}
	}

	s := stats(t)
	reqs := int(s["requests"].(float64))
	total := int(s["rounds_total"].(float64))
	if reqs != n {
		t.Fatalf("server counted %d requests over the walk, expected %d", reqs, n)
	}
	if total != n*rounds {
		t.Fatalf("server performed %d hash rounds for %d requests at %d rounds each; expected %d",
			total, reqs, rounds, n*rounds)
	}
	// The resource meter is the primary instrument of this experiment. A meter
	// that reads zero after a full walk of the id space is broken, and a broken
	// meter must fail here rather than produce a suspiciously cheap cell later.
	if cpu := s["cpu_s"].(float64); cpu <= 0 {
		t.Fatalf("resource meter read cpu_s=%v after %d requests", cpu, n)
	}
	if rss := s["peak_rss_bytes"].(float64); rss <= 0 {
		t.Fatalf("resource meter read peak_rss_bytes=%v", rss)
	}
	t.Logf("%d ids byte-identical to locally recomputed truth; %d hash rounds; cpu_s=%.3f rss=%.0fMiB",
		n, total, s["cpu_s"].(float64), s["peak_rss_bytes"].(float64)/1048576)
}

func TestBadIDIsRejected(t *testing.T) {
	c := &http.Client{Timeout: 10 * time.Second}
	res, err := c.Get(appURL() + "/work?id=notanumber")
	if err != nil {
		t.Skipf("no app: %v", err)
	}
	defer func() { _ = res.Body.Close() }()
	if res.StatusCode != http.StatusBadRequest {
		t.Fatalf("bad id returned %d, want 400", res.StatusCode)
	}
}
