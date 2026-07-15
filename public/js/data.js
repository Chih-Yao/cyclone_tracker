const CROSS_ORIGIN_MESSAGE = "只允許讀取本站的預報資料";
const UNAVAILABLE_MESSAGE = "目前無法取得預報資料";
const MALFORMED_MESSAGE = "預報資料格式不正確";

function sameOriginUrl(path) {
  let url;
  try {
    url = new URL(path, window.location.href);
  } catch {
    throw new Error(CROSS_ORIGIN_MESSAGE);
  }

  if (url.username || url.password || url.origin !== window.location.origin) {
    throw new Error(CROSS_ORIGIN_MESSAGE);
  }
  return url;
}

async function fetchJson(path, fetchOptions) {
  const url = sameOriginUrl(path);
  let response;
  try {
    response = await fetch(url, fetchOptions);
  } catch {
    throw new Error(UNAVAILABLE_MESSAGE);
  }

  if (!response.ok) {
    throw new Error(UNAVAILABLE_MESSAGE);
  }

  try {
    return await response.json();
  } catch {
    throw new Error(MALFORMED_MESSAGE);
  }
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasValidManifestShape(value) {
  return (
    isRecord(value) &&
    value.schema_version === 1 &&
    Array.isArray(value.sources) &&
    value.sources.every(
      (source) =>
        isRecord(source) &&
        Array.isArray(source.cycles) &&
        source.cycles.every((cycle) => isRecord(cycle) && Array.isArray(cycle.storms)),
    )
  );
}

function hasValidCycleShape(value) {
  return (
    isRecord(value) &&
    value.schema_version === 1 &&
    Array.isArray(value.storms) &&
    value.storms.every(
      (storm) =>
        isRecord(storm) &&
        Array.isArray(storm.members) &&
        storm.members.every((member) => isRecord(member) && Array.isArray(member.points)) &&
        isRecord(storm.mean) &&
        Array.isArray(storm.mean.points),
    )
  );
}

export async function loadManifest(path = "/data/manifest.json", fetchOptions) {
  const manifest = await fetchJson(path, fetchOptions);
  if (!hasValidManifestShape(manifest)) {
    throw new Error(MALFORMED_MESSAGE);
  }
  return manifest;
}

export async function loadCycle(href, fetchOptions) {
  const cycle = await fetchJson(href, fetchOptions);
  if (!hasValidCycleShape(cycle)) {
    throw new Error(MALFORMED_MESSAGE);
  }
  return cycle;
}
