// Package work is the handler's unit of work: a deterministic, CPU-bound
// function of the request id alone.
//
// It is deterministic on purpose. The two arms of this experiment run the same
// image, so equal work is guaranteed by construction — but "guaranteed by
// construction" is exactly the claim that quietly stops being true, so the
// bench gate re-derives every response body here, in-process, and asserts the
// server returned those bytes. That is only possible because there is no clock,
// no randomness and no I/O anywhere in this file.
package work

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
)

// PayloadBytes is the size of the buffer each round hashes. 1 KiB is small
// enough that the whole working set stays in L1 and the cost per round is
// stable, which is what makes CPU-seconds per request a usable constant.
const PayloadBytes = 1024

// Payload is a byte-exact function of the id: same id, same bytes, on any
// machine, in any process.
func Payload(id uint64) []byte {
	b := make([]byte, PayloadBytes)
	for i := 0; i+8 <= len(b); i += 8 {
		binary.LittleEndian.PutUint64(b[i:], id*0x9E3779B97F4A7C15+uint64(i))
	}
	return b
}

// Do chains `rounds` SHA-256 compressions over the id's payload and returns the
// final digest in lowercase hex. The chain is serial, so the work cannot be
// optimised away or parallelised behind our back.
func Do(id uint64, rounds int) string {
	buf := Payload(id)
	sum := sha256.Sum256(buf)
	for i := 1; i < rounds; i++ {
		copy(buf, sum[:])
		sum = sha256.Sum256(buf)
	}
	return hex.EncodeToString(sum[:])
}
