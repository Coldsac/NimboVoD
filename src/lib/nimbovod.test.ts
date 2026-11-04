import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { SAMPLE_LIBRARY, SAMPLE_PLAYLIST } from "./sample.ts";
import {
  isPrivateAddress,
  normalizeTitle,
  parseEpisode,
  parseLibraryText,
  parseM3u,
  planLibrary,
  strmFileMap,
} from "./nimbovod.ts";

const OPTIONS = {
  layout: "group" as const,
  stripQuality: true,
  useYears: true,
  matchFilenames: true,
};

describe("parseM3u", () => {
  it("keeps commas that belong to the title", () => {
    const parsed = parseM3u('#EXTM3U\n#EXTINF:-1 group-title="Movies",Hello, World (1999)\nhttps://example.com/a\n');
    assert.equal(parsed.entries.length, 1);
    assert.equal(parsed.entries[0]?.title, "Hello, World (1999)");
    assert.equal(parsed.entries[0]?.group, "Movies");
  });

  it("uses EXTGRP when group-title is missing", () => {
    const parsed = parseM3u("#EXTM3U\n#EXTGRP:Live\n#EXTINF:-1,City News\nhttps://example.com/news\n");
    assert.equal(parsed.entries[0]?.group, "Live");
    assert.equal(parsed.entries[0]?.title, "City News");
  });
});

describe("matching", () => {
  it("normalizes quality tags and a leading article", () => {
    assert.deepEqual(normalizeTitle("The Matrix (1999) 1080p"), { key: "matrix", year: 1999 });
    assert.deepEqual(normalizeTitle("1917"), { key: "1917", year: 1917 });
  });

  it("parses season and episode markers", () => {
    assert.deepEqual(parseEpisode("The Office S01E03 Diversity Day"), {
      show: "The Office",
      season: 1,
      episode: 3,
      rest: "Diversity Day",
    });
  });

  it("drops library matches and keeps different years and missing episodes", () => {
    const entries = parseM3u(SAMPLE_PLAYLIST).entries;
    const plan = planLibrary(entries, SAMPLE_LIBRARY, OPTIONS);
    const byTitle = new Map(plan.files.map((file) => [file.title, file]));

    assert.equal(byTitle.get("The Matrix (1999)")?.disposition, "drop");
    assert.equal(byTitle.get("Inception (2010) 1080p")?.disposition, "drop");
    assert.equal(byTitle.get("Inception.2010.UHD.BluRay")?.disposition, "drop");
    assert.equal(byTitle.get("Dune (2021)")?.disposition, "drop");
    assert.equal(byTitle.get("Dune (1984)")?.disposition, "keep");
    assert.equal(byTitle.get("Blade Runner 2049")?.disposition, "keep");
    assert.equal(byTitle.get("The Office S01E01")?.disposition, "drop");
    assert.equal(byTitle.get("The Office S01E03")?.disposition, "keep");
    assert.equal(byTitle.get("The Office (2005)")?.disposition, "drop");
    assert.equal(byTitle.get("Severance S01E02")?.disposition, "keep");
    assert.equal(byTitle.get("City News")?.disposition, "keep");
    assert.equal(plan.duplicates, 1);
    assert.equal(plan.kept, 5);
    assert.ok(!("Movies/The Matrix (1999).strm" in strmFileMap(plan.files)));
    assert.match(strmFileMap(plan.files)["Classic Movies/Dune (1984).strm"] ?? "", /dune-1984/);
  });

  it("keeps a title when years disagree only if year checks are on", () => {
    const entries = parseM3u('#EXTM3U\n#EXTINF:-1,Dune (1984)\nhttps://example.com/dune\n').entries;
    const strict = planLibrary(entries, SAMPLE_LIBRARY, OPTIONS);
    const loose = planLibrary(entries, SAMPLE_LIBRARY, { ...OPTIONS, useYears: false });
    assert.equal(strict.files[0]?.disposition, "keep");
    assert.equal(loose.files[0]?.disposition, "drop");
  });
});

describe("parseLibraryText", () => {
  it("reads an Emby export and a plain title list", () => {
    const fromJson = parseLibraryText(
      JSON.stringify({
        items: [{ name: "Heat", year: 1995, type: "Movie", path: "/media/Heat (1995).mkv" }],
      }),
    );
    assert.equal(fromJson[0]?.name, "Heat");
    assert.equal(fromJson[0]?.year, 1995);

    const fromApi = parseLibraryText(
      JSON.stringify({
        Items: [{ Name: "The Matrix", Type: "Movie", ProductionYear: 1999, Path: "/m/a.mkv" }],
      }),
    );
    assert.equal(fromApi[0]?.type, "Movie");

    const fromText = parseLibraryText("The Office S01E02\nArrival (2016)\n");
    assert.equal(fromText[0]?.type, "Episode");
    assert.equal(fromText[0]?.season, 1);
    assert.equal(fromText[0]?.episode, 2);
    assert.equal(fromText[1]?.type, "Movie");
    assert.equal(fromText[1]?.year, 2016);
  });
});

describe("isPrivateAddress", () => {
  it("blocks home and link-local ranges", () => {
    assert.equal(isPrivateAddress("192.168.1.20"), true);
    assert.equal(isPrivateAddress("10.0.0.5"), true);
    assert.equal(isPrivateAddress("127.0.0.1"), true);
    assert.equal(isPrivateAddress("169.254.169.254"), true);
    assert.equal(isPrivateAddress("172.16.0.4"), true);
    assert.equal(isPrivateAddress("8.8.8.8"), false);
    assert.equal(isPrivateAddress("::1"), true);
  });
});
