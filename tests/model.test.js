// Runs Model.js exactly as QML loads it (plain script, top-level functions)
// and checks it against a real helper snapshot. Run with: node --test tests/
const test = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const vm = require("node:vm")
const { execFileSync } = require("node:child_process")

const root = path.join(__dirname, "..")
const Model = vm.createContext({})
vm.runInContext(fs.readFileSync(path.join(root, "Model.js"), "utf8"), Model)

// Produce a snapshot through the Python collector so both halves are tested
// against the same contract.
function sampleSnapshot() {
  const script = `
import json, sys
sys.path.insert(0, "tests"); sys.path.insert(0, "lib")
from test_snapshot import collect
print(json.dumps(collect()[0]))
`
  return execFileSync("python3", ["-c", script], { cwd: root, encoding: "utf8" })
}

test("parseSnapshot accepts helper output", () => {
  const parsed = Model.parseSnapshot(sampleSnapshot())
  assert.equal(parsed.ok, true)
  assert.equal(Model.overallState(parsed.snapshot, false), "warning")
  assert.equal(Model.heroMeta(parsed.snapshot, "warning"), "AP Garage is offline")
})

test("parseSnapshot rejects garbage and future schemas", () => {
  assert.equal(Model.parseSnapshot("").ok, false)
  assert.equal(Model.parseSnapshot("{nope").ok, false)
  assert.match(Model.parseSnapshot('{"schema": 2}').error, /update the plugin/)
})

test("overall state distinguishes stale from down", () => {
  assert.equal(Model.overallState(null, false), "loading")
  assert.equal(Model.overallState({ status: "down" }, false), "down")
  assert.equal(Model.overallState({ status: "down" }, true), "stale")
  assert.equal(Model.overallState({ status: "unconfigured" }, false), "unconfigured")
})

test("bar label modes", () => {
  const snap = Model.parseSnapshot(sampleSnapshot()).snapshot
  assert.equal(Model.barLabel(snap, "none"), "")
  assert.equal(Model.barLabel(snap, "clients"), "3")
  assert.equal(Model.barLabel(snap, "devices"), "3/4")
  assert.equal(Model.barLabel(snap, "latency"), "12ms")
})

test("formatting helpers", () => {
  assert.equal(Model.formatBps(18000000), "18.0 Mbps")
  assert.equal(Model.formatBps(940500000), "941 Mbps")
  assert.equal(Model.formatBps(null), "—")
  assert.equal(Model.formatDuration(864000), "10d 0h")
  assert.equal(Model.formatDuration(3700), "1h 1m")
  assert.equal(Model.formatAge("2026-01-01T00:00:00Z", Date.parse("2026-01-01T00:00:10Z")), "just now")
  assert.equal(Model.formatAge("2026-01-01T00:00:00Z", Date.parse("2026-01-01T00:05:00Z")), "5m ago")
})

test("self row reads like the network panel", () => {
  const snap = Model.parseSnapshot(sampleSnapshot()).snapshot
  assert.equal(Model.selfTitle(snap.self), "HomeNet")
  assert.equal(Model.selfSubtitle(snap.self), "via AP Salon · 5 GHz ch 40 · -58 dBm")
})

test("client filter matches name, ip, mac, ssid", () => {
  const snap = Model.parseSnapshot(sampleSnapshot()).snapshot
  assert.equal(Model.filterClients(snap.clients, "nas").length, 1)
  assert.equal(Model.filterClients(snap.clients, "192.168.2.").length, 1)
  assert.equal(Model.filterClients(snap.clients, "guest").length, 1)
  assert.equal(Model.filterClients(snap.clients, "").length, 3)
})

test("sections setting is filtered and deduplicated", () => {
  assert.deepEqual(Array.from(Model.sectionList(["clients", "bogus", "clients", "wan"])), ["clients", "wan"])
  assert.deepEqual(Array.from(Model.sectionList(null)), ["wan", "self", "devices", "clients"])
  assert.deepEqual(Array.from(Model.sectionList([])), ["wan", "self", "devices", "clients"])
})

test("skip args", () => {
  assert.deepEqual(Array.from(Model.skipArgs(true, true)), [])
  assert.deepEqual(Array.from(Model.skipArgs(false, false)), ["--skip", "stats,legacy"])
})
