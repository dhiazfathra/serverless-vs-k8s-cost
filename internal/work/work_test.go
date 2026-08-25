package work

import "testing"

func TestDoIsDeterministic(t *testing.T) {
	for _, id := range []uint64{0, 1, 7, 999, 1 << 40} {
		a, b := Do(id, 64), Do(id, 64)
		if a != b {
			t.Fatalf("id %d: %s != %s", id, a, b)
		}
	}
}

func TestDoSeparatesIDsAndRounds(t *testing.T) {
	if Do(1, 64) == Do(2, 64) {
		t.Fatal("different ids produced the same digest")
	}
	if Do(1, 64) == Do(1, 65) {
		t.Fatal("different round counts produced the same digest")
	}
}

func TestPayloadIsFixedSizeAndIDDependent(t *testing.T) {
	p := Payload(3)
	if len(p) != PayloadBytes {
		t.Fatalf("payload is %d bytes, want %d", len(p), PayloadBytes)
	}
	if string(p) == string(Payload(4)) {
		t.Fatal("payload does not depend on the id")
	}
}

func TestRoundsAreSerialNotSkipped(t *testing.T) {
	// One round is a plain hash of the payload; anything more must differ from
	// it, or the loop is being skipped.
	seen := map[string]bool{}
	for r := 1; r <= 8; r++ {
		d := Do(9, r)
		if seen[d] {
			t.Fatalf("round %d repeated an earlier digest", r)
		}
		seen[d] = true
	}
}
