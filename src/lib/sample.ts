import type { EmbyItem } from "@/lib/nimbovod";

export const SAMPLE_PLAYLIST = `#EXTM3U
#EXTINF:-1 tvg-id="matrix" group-title="Movies",The Matrix (1999)
https://example.com/streams/the-matrix-1999
#EXTINF:-1 group-title="Movies",Inception (2010) 1080p
https://example.com/streams/inception-2010
#EXTINF:-1 group-title="Movies",Inception.2010.UHD.BluRay
https://example.com/streams/inception-uhd
#EXTINF:-1 group-title="Movies",Dune (2021)
https://example.com/streams/dune-2021
#EXTINF:-1 group-title="Classic Movies",Dune (1984)
https://example.com/streams/dune-1984
#EXTINF:-1 group-title="Movies",Arrival (2016)
https://example.com/streams/arrival
#EXTINF:-1 group-title="Movies",Blade Runner 2049
https://example.com/streams/blade-runner-2049
#EXTINF:-1 group-title="Movies",Heat (1995)
https://example.com/streams/heat
#EXTINF:-1 group-title="Movies",The Matrix (1999)
https://example.com/streams/the-matrix-1999
#EXTINF:-1 group-title="TV",The Office (2005)
https://example.com/streams/the-office
#EXTINF:-1 group-title="TV",The Office S01E01
https://example.com/streams/the-office-s01e01
#EXTINF:-1 group-title="TV",The Office S01E03
https://example.com/streams/the-office-s01e03
#EXTINF:-1 group-title="TV",Severance S01E01
https://example.com/streams/severance-s01e01
#EXTINF:-1 group-title="TV",Severance S01E02
https://example.com/streams/severance-s01e02
#EXTINF:-1 group-title="Live TV",City News
https://example.com/streams/city-news
`;

export const SAMPLE_LIBRARY: EmbyItem[] = [
  { name: "The Matrix", year: 1999, type: "Movie", path: "/media/movies/The Matrix (1999).mkv" },
  { name: "Inception", year: 2010, type: "Movie", path: "/media/movies/Inception (2010).mkv" },
  { name: "Dune", year: 2021, type: "Movie", path: "/media/movies/Dune (2021).mkv" },
  { name: "Arrival", year: 2016, type: "Movie" },
  { name: "Heat", year: 1995, type: "Movie" },
  { name: "The Office", year: 2005, type: "Series" },
  {
    name: "Pilot",
    year: 2005,
    type: "Episode",
    seriesName: "The Office",
    season: 1,
    episode: 1,
  },
  {
    name: "Diversity Day",
    year: 2005,
    type: "Episode",
    seriesName: "The Office",
    season: 1,
    episode: 2,
  },
  { name: "Severance", year: 2022, type: "Series" },
  {
    name: "Good News About Hell",
    year: 2022,
    type: "Episode",
    seriesName: "Severance",
    season: 1,
    episode: 1,
  },
];

export const SAMPLE_ENTRIES_NOTE =
  "Sample library already has The Matrix (1999), Inception, Dune (2021), and The Office S01E01. Dune (1984) and The Office S01E03 stay because they are not the same item.";
