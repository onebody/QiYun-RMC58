// NET is one command family with two reply shapes: the queries answer with a
// "NET " payload, the setters answer plain OK/ERR. Matching "NET " on a setter
// made every Save line sit out its 3 s timeout and look like a dead relay.
import { describe, expect, it } from "vitest";
import { replyMatcher } from "./device";

const text = (line: string) => ({ kind: "text" as const, line });

describe("replyMatcher: NET queries", () => {
  it("NET STATUS ends on the NET payload", () => {
    const match = replyMatcher("NET STATUS");
    expect(match(text('NET {"on":true}'))).toBe(true);
    expect(match(text("ERR net_status"))).toBe(true);
    expect(match(text("OK"))).toBe(false);
  });

  it("NET SCAN ends on the NET array", () => {
    const match = replyMatcher("NET SCAN");
    expect(match(text('NET ["ap-a"]'))).toBe(true);
    expect(match(text("ERR net_scan"))).toBe(true);
    expect(match(text("OK"))).toBe(false);
  });
});

describe("replyMatcher: NET setters", () => {
  const setters = [
    "NET SSID home",
    "NET PASS secret",
    "NET URL ws://192.168.1.10:8080/node",
    "NET ID relay-1",
    "NET SAVE",
    "NET ON",
    "NET OFF",
    "NET AP",
    "NET AP OFF",
  ];
  for (const cmd of setters) {
    it(`${cmd} ends on OK/ERR, never on a NET line`, () => {
      const match = replyMatcher(cmd);
      expect(match(text("OK"))).toBe(true);
      expect(match(text("OK ENABLE 1"))).toBe(true);
      expect(match(text("ERR net_ssid"))).toBe(true);
      expect(match(text('NET {"on":true}'))).toBe(false);
    });
  }
});
