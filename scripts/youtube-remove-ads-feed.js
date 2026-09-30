/*
 * YouTube feed ad cleanup for Stash/Loon.
 *
 * iOS renders recommendations and search results as nested protobuf
 * messages. Ads may contain pagead/AD_CPN markers, while newer promoted
 * cards can only expose a localized "赞助商广告" label.
 */

const SCRIPT_VERSION = "20260930e";
const RICH_ITEM_PARENT_FIELDS = [50195462, 51431404];
const RICH_ITEM_FIELD = 1;
const ITEM_WRAPPER_FIELDS = [49399797];
const GUIDE_ITEM_FIELDS = [4, 6];
const GUIDE_SECTION_FIELD = 117866661;
const GUIDE_SECTION_ITEM_FIELD = 1;
const GUIDE_ENTRY_FIELDS = [318370163, 117501096];
const GUIDE_ENTRY_BROWSE_ID_FIELD = 1;
const GUIDE_BLOCKED_BROWSE_IDS = ["FEshorts", "FEuploads"].map(toUtf8Bytes);
const MAX_DEPTH = 32;
const AD_MARKERS = [
  "pagead",
  "AD_CPN",
  "BAD_CPN",
  "[VIEWABILITY]",
  "赞助商广告",
  "贊助商廣告",
  "Sponsored",
  "promotedVideo",
  "engagement_panel_about_this_ad_",
  "skip_ad_on_block",
  "aboutthisad",
  "myadcenter"
].map(toUtf8Bytes);
const CONFIRMED_AD_MARKERS = [
  "pagead",
  "AD_CPN",
  "BAD_CPN",
  "赞助商广告",
  "贊助商廣告",
  "engagement_panel_about_this_ad_",
  "skip_ad_on_block",
  "aboutthisad",
  "myadcenter",
  "yt-ads-web-view-id"
].map(toUtf8Bytes);

function toUtf8Bytes(value) {
  const bytes = [];

  for (let index = 0; index < value.length; index += 1) {
    let codePoint = value.charCodeAt(index);

    if (
      codePoint >= 0xd800 &&
      codePoint <= 0xdbff &&
      index + 1 < value.length
    ) {
      const low = value.charCodeAt(index + 1);
      if (low >= 0xdc00 && low <= 0xdfff) {
        codePoint =
          0x10000 + ((codePoint - 0xd800) << 10) + (low - 0xdc00);
        index += 1;
      }
    }

    if (codePoint < 0x80) {
      bytes.push(codePoint);
    } else if (codePoint < 0x800) {
      bytes.push(0xc0 | (codePoint >> 6));
      bytes.push(0x80 | (codePoint & 0x3f));
    } else if (codePoint < 0x10000) {
      bytes.push(0xe0 | (codePoint >> 12));
      bytes.push(0x80 | ((codePoint >> 6) & 0x3f));
      bytes.push(0x80 | (codePoint & 0x3f));
    } else {
      bytes.push(0xf0 | (codePoint >> 18));
      bytes.push(0x80 | ((codePoint >> 12) & 0x3f));
      bytes.push(0x80 | ((codePoint >> 6) & 0x3f));
      bytes.push(0x80 | (codePoint & 0x3f));
    }
  }

  return new Uint8Array(bytes);
}

function toBytes(value) {
  if (value instanceof Uint8Array) {
    return value;
  }
  if (value instanceof ArrayBuffer) {
    return new Uint8Array(value);
  }
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }
  return null;
}

function readVarint(bytes, start, end) {
  let value = 0;
  let shift = 0;
  let position = start;

  while (position < end && shift < 70) {
    const byte = bytes[position];
    position += 1;
    value += (byte & 0x7f) * Math.pow(2, shift);
    if ((byte & 0x80) === 0) {
      return { value: value, position: position };
    }
    shift += 7;
  }

  return null;
}

function encodeVarint(value) {
  const bytes = [];
  let remaining = value;

  while (remaining > 127) {
    bytes.push((remaining % 128) | 0x80);
    remaining = Math.floor(remaining / 128);
  }
  bytes.push(remaining);

  return new Uint8Array(bytes);
}

function concatChunks(chunks) {
  let length = 0;
  for (let index = 0; index < chunks.length; index += 1) {
    length += chunks[index].length;
  }

  const output = new Uint8Array(length);
  let offset = 0;
  for (let index = 0; index < chunks.length; index += 1) {
    output.set(chunks[index], offset);
    offset += chunks[index].length;
  }
  return output;
}

function containsAt(bytes, start, end, marker) {
  if (marker.length === 0 || start + marker.length > end) {
    return false;
  }

  for (let index = start; index + marker.length <= end; index += 1) {
    let matches = true;
    for (let markerIndex = 0; markerIndex < marker.length; markerIndex += 1) {
      if (bytes[index + markerIndex] !== marker[markerIndex]) {
        matches = false;
        break;
      }
    }
    if (matches) {
      return true;
    }
  }
  return false;
}

function hasAdMarker(bytes, start, end) {
  for (let index = 0; index < AD_MARKERS.length; index += 1) {
    if (containsAt(bytes, start, end, AD_MARKERS[index])) {
      return true;
    }
  }
  return false;
}

function hasConfirmedAdMarker(bytes, start, end) {
  for (let index = 0; index < CONFIRMED_AD_MARKERS.length; index += 1) {
    if (containsAt(bytes, start, end, CONFIRMED_AD_MARKERS[index])) {
      return true;
    }
  }
  return false;
}

function bytesEqual(bytes, start, end, expected) {
  if (end - start !== expected.length) {
    return false;
  }

  for (let index = 0; index < expected.length; index += 1) {
    if (bytes[start + index] !== expected[index]) {
      return false;
    }
  }
  return true;
}

function isBlockedGuideEntry(bytes, start, end) {
  let position = start;

  while (position < end) {
    const tag = readVarint(bytes, position, end);
    if (!tag) {
      return false;
    }

    const fieldNumber = Math.floor(tag.value / 8);
    const wireType = tag.value % 8;
    position = tag.position;

    if (wireType === 0) {
      const value = readVarint(bytes, position, end);
      if (!value) {
        return false;
      }
      position = value.position;
      continue;
    }
    if (wireType === 1) {
      position += 8;
      continue;
    }
    if (wireType === 2) {
      const length = readVarint(bytes, position, end);
      if (!length) {
        return false;
      }

      const payloadStart = length.position;
      const payloadEnd = payloadStart + length.value;
      if (payloadEnd > end) {
        return false;
      }

      if (fieldNumber === GUIDE_ENTRY_BROWSE_ID_FIELD) {
        for (let index = 0; index < GUIDE_BLOCKED_BROWSE_IDS.length; index += 1) {
          if (
            bytesEqual(
              bytes,
              payloadStart,
              payloadEnd,
              GUIDE_BLOCKED_BROWSE_IDS[index]
            )
          ) {
            return true;
          }
        }
      }

      position = payloadEnd;
      continue;
    }
    if (wireType === 5) {
      position += 4;
      continue;
    }
    return false;
  }

  return false;
}

function shouldRemoveGuideRendererItem(bytes, start, end) {
  let position = start;

  while (position < end) {
    const tag = readVarint(bytes, position, end);
    if (!tag) {
      return false;
    }

    const fieldNumber = Math.floor(tag.value / 8);
    const wireType = tag.value % 8;
    position = tag.position;

    if (wireType === 0) {
      const value = readVarint(bytes, position, end);
      if (!value) {
        return false;
      }
      position = value.position;
      continue;
    }
    if (wireType === 1) {
      position += 8;
      continue;
    }
    if (wireType === 2) {
      const length = readVarint(bytes, position, end);
      if (!length) {
        return false;
      }

      const payloadStart = length.position;
      const payloadEnd = payloadStart + length.value;
      if (payloadEnd > end) {
        return false;
      }

      if (
        GUIDE_ENTRY_FIELDS.indexOf(fieldNumber) !== -1 &&
        isBlockedGuideEntry(bytes, payloadStart, payloadEnd)
      ) {
        return true;
      }

      position = payloadEnd;
      continue;
    }
    if (wireType === 5) {
      position += 4;
      continue;
    }
    return false;
  }

  return false;
}

function cleanGuideSection(bytes, start, end) {
  const chunks = [];
  let changed = false;
  let position = start;

  while (position < end) {
    const fieldStart = position;
    const tag = readVarint(bytes, position, end);
    if (!tag) {
      return { bytes: bytes.subarray(start, end), changed: false };
    }

    const fieldNumber = Math.floor(tag.value / 8);
    const wireType = tag.value % 8;
    position = tag.position;

    if (wireType === 0) {
      const value = readVarint(bytes, position, end);
      if (!value) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }
      position = value.position;
      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }
    if (wireType === 1) {
      position += 8;
      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }
    if (wireType === 2) {
      const length = readVarint(bytes, position, end);
      if (!length) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }

      const payloadStart = length.position;
      const payloadEnd = payloadStart + length.value;
      if (payloadEnd > end) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }
      position = payloadEnd;

      if (
        fieldNumber === GUIDE_SECTION_ITEM_FIELD &&
        shouldRemoveGuideRendererItem(bytes, payloadStart, payloadEnd)
      ) {
        changed = true;
        continue;
      }

      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }
    if (wireType === 5) {
      position += 4;
      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }
    return { bytes: bytes.subarray(start, end), changed: false };
  }

  if (!changed) {
    return { bytes: bytes.subarray(start, end), changed: false };
  }
  return { bytes: concatChunks(chunks), changed: true };
}

function cleanGuideItem(bytes, start, end) {
  const chunks = [];
  let changed = false;
  let position = start;

  while (position < end) {
    const fieldStart = position;
    const tag = readVarint(bytes, position, end);
    if (!tag) {
      return { bytes: bytes.subarray(start, end), changed: false };
    }

    const fieldNumber = Math.floor(tag.value / 8);
    const wireType = tag.value % 8;
    position = tag.position;

    if (wireType === 0) {
      const value = readVarint(bytes, position, end);
      if (!value) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }
      position = value.position;
      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }
    if (wireType === 1) {
      position += 8;
      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }
    if (wireType === 2) {
      const length = readVarint(bytes, position, end);
      if (!length) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }

      const payloadStart = length.position;
      const payloadEnd = payloadStart + length.value;
      if (payloadEnd > end) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }
      position = payloadEnd;

      if (fieldNumber === GUIDE_SECTION_FIELD) {
        const cleaned = cleanGuideSection(bytes, payloadStart, payloadEnd);
        if (cleaned.changed) {
          chunks.push(bytes.subarray(fieldStart, tag.position));
          chunks.push(encodeVarint(cleaned.bytes.length));
          chunks.push(cleaned.bytes);
          changed = true;
          continue;
        }
      }

      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }
    if (wireType === 5) {
      position += 4;
      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }
    return { bytes: bytes.subarray(start, end), changed: false };
  }

  if (!changed) {
    return { bytes: bytes.subarray(start, end), changed: false };
  }
  return { bytes: concatChunks(chunks), changed: true };
}

function cleanGuideNavigation(bytes, start, end) {
  const chunks = [];
  let changed = false;
  let position = start;

  while (position < end) {
    const fieldStart = position;
    const tag = readVarint(bytes, position, end);
    if (!tag) {
      return { bytes: bytes.subarray(start, end), changed: false };
    }

    const fieldNumber = Math.floor(tag.value / 8);
    const wireType = tag.value % 8;
    position = tag.position;

    if (wireType === 0) {
      const value = readVarint(bytes, position, end);
      if (!value) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }
      position = value.position;
      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }
    if (wireType === 1) {
      position += 8;
      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }
    if (wireType === 2) {
      const length = readVarint(bytes, position, end);
      if (!length) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }

      const payloadStart = length.position;
      const payloadEnd = payloadStart + length.value;
      if (payloadEnd > end) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }
      position = payloadEnd;

      if (GUIDE_ITEM_FIELDS.indexOf(fieldNumber) !== -1) {
        const cleaned = cleanGuideItem(bytes, payloadStart, payloadEnd);
        if (cleaned.changed) {
          chunks.push(bytes.subarray(fieldStart, tag.position));
          chunks.push(encodeVarint(cleaned.bytes.length));
          chunks.push(cleaned.bytes);
          changed = true;
          continue;
        }
      }

      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }
    if (wireType === 5) {
      position += 4;
      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }
    return { bytes: bytes.subarray(start, end), changed: false };
  }

  if (!changed) {
    return { bytes: bytes.subarray(start, end), changed: false };
  }
  return { bytes: concatChunks(chunks), changed: true };
}

function endpointName() {
  const request = typeof $request !== "undefined" ? $request : null;
  const url = request && typeof request.url === "string" ? request.url : "";
  const match = /\/youtubei\/v1\/([^?]+)/.exec(url);
  return match ? match[1] : "unknown";
}

function hasRepeatedAdRichItems(bytes, start, end) {
  let richItemCount = 0;
  let adRichItemCount = 0;
  let position = start;

  while (position < end) {
    const tag = readVarint(bytes, position, end);
    if (!tag) {
      return false;
    }

    const fieldNumber = Math.floor(tag.value / 8);
    const wireType = tag.value % 8;
    position = tag.position;

    if (wireType === 0) {
      const value = readVarint(bytes, position, end);
      if (!value) {
        return false;
      }
      position = value.position;
    } else if (wireType === 1) {
      position += 8;
    } else if (wireType === 2) {
      const length = readVarint(bytes, position, end);
      if (!length) {
        return false;
      }
      const payloadStart = length.position;
      const payloadEnd = payloadStart + length.value;
      if (payloadEnd > end) {
        return false;
      }

      if (fieldNumber === RICH_ITEM_FIELD) {
        richItemCount += 1;
        if (hasAdMarker(bytes, payloadStart, payloadEnd)) {
          adRichItemCount += 1;
        }
      }
      position = payloadEnd;
    } else if (wireType === 5) {
      position += 4;
    } else {
      return false;
    }

    if (position > end) {
      return false;
    }
  }

  return richItemCount >= 2 && adRichItemCount > 0;
}

function cleanRichItems(bytes, start, end) {
  const chunks = [];
  let changed = false;
  let position = start;

  while (position < end) {
    const fieldStart = position;
    const tag = readVarint(bytes, position, end);
    if (!tag) {
      return { bytes: bytes.subarray(start, end), changed: false };
    }

    const fieldNumber = Math.floor(tag.value / 8);
    const wireType = tag.value % 8;
    position = tag.position;
    if (fieldNumber <= 0) {
      return { bytes: bytes.subarray(start, end), changed: false };
    }

    if (wireType === 0) {
      const value = readVarint(bytes, position, end);
      if (!value) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }
      position = value.position;
      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }

    if (wireType === 1) {
      if (position + 8 > end) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }
      position += 8;
      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }

    if (wireType === 2) {
      const length = readVarint(bytes, position, end);
      if (!length) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }

      const payloadStart = length.position;
      const payloadEnd = payloadStart + length.value;
      if (payloadEnd > end) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }
      position = payloadEnd;

      if (
        fieldNumber === RICH_ITEM_FIELD &&
        hasAdMarker(bytes, payloadStart, payloadEnd)
      ) {
        changed = true;
        continue;
      }

      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }

    if (wireType === 5) {
      if (position + 4 > end) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }
      position += 4;
      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }

    return { bytes: bytes.subarray(start, end), changed: false };
  }

  if (!changed) {
    return { bytes: bytes.subarray(start, end), changed: false };
  }
  return { bytes: concatChunks(chunks), changed: true };
}

function isRichItemParent(fieldNumber) {
  for (let index = 0; index < RICH_ITEM_PARENT_FIELDS.length; index += 1) {
    if (RICH_ITEM_PARENT_FIELDS[index] === fieldNumber) {
      return true;
    }
  }
  return false;
}

function isItemWrapper(fieldNumber) {
  for (let index = 0; index < ITEM_WRAPPER_FIELDS.length; index += 1) {
    if (ITEM_WRAPPER_FIELDS[index] === fieldNumber) {
      return true;
    }
  }
  return false;
}

function cleanMessage(bytes, start, end, depth) {
  const chunks = [];
  const genericRichItemList = hasRepeatedAdRichItems(bytes, start, end);
  let changed = false;
  let position = start;

  while (position < end) {
    const fieldStart = position;
    const tag = readVarint(bytes, position, end);
    if (!tag) {
      return { bytes: bytes.subarray(start, end), changed: false };
    }

    const fieldNumber = Math.floor(tag.value / 8);
    const wireType = tag.value % 8;
    position = tag.position;
    if (fieldNumber <= 0) {
      return { bytes: bytes.subarray(start, end), changed: false };
    }

    if (wireType === 0) {
      const value = readVarint(bytes, position, end);
      if (!value) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }
      position = value.position;
      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }

    if (wireType === 1) {
      if (position + 8 > end) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }
      position += 8;
      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }

    if (wireType === 2) {
      const length = readVarint(bytes, position, end);
      if (!length) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }

      const payloadStart = length.position;
      const payloadEnd = payloadStart + length.value;
      if (payloadEnd > end) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }
      position = payloadEnd;

      const payloadHasAdMarker = hasAdMarker(bytes, payloadStart, payloadEnd);
      const payloadHasConfirmedAdMarker = hasConfirmedAdMarker(
        bytes,
        payloadStart,
        payloadEnd
      );

      /*
       * iOS sometimes returns an ad as a standalone item wrapper instead of
       * a repeated rich-item child. Clean the wrapper first; if nothing inside
       * can be removed, drop the wrapper itself.
       */
      if (isItemWrapper(fieldNumber) && payloadHasConfirmedAdMarker) {
        if (depth < MAX_DEPTH) {
          const nested = cleanMessage(
            bytes,
            payloadStart,
            payloadEnd,
            depth + 1
          );
          if (nested.changed) {
            chunks.push(bytes.subarray(fieldStart, tag.position));
            chunks.push(encodeVarint(nested.bytes.length));
            chunks.push(nested.bytes);
            changed = true;
            continue;
          }
        }

        changed = true;
        continue;
      }

      if (isRichItemParent(fieldNumber) && payloadHasAdMarker) {
        const cleaned = cleanRichItems(bytes, payloadStart, payloadEnd);
        if (cleaned.changed) {
          chunks.push(bytes.subarray(fieldStart, tag.position));
          chunks.push(encodeVarint(cleaned.bytes.length));
          chunks.push(cleaned.bytes);
          changed = true;
          continue;
        }

        changed = true;
        continue;
      }

      if (
        fieldNumber === RICH_ITEM_FIELD &&
        payloadHasAdMarker &&
        genericRichItemList
      ) {
        changed = true;
        continue;
      }

      if (depth < MAX_DEPTH && payloadHasAdMarker) {
        const nested = cleanMessage(bytes, payloadStart, payloadEnd, depth + 1);
        if (nested.changed) {
          chunks.push(bytes.subarray(fieldStart, tag.position));
          chunks.push(encodeVarint(nested.bytes.length));
          chunks.push(nested.bytes);
          changed = true;
          continue;
        }
      }

      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }

    if (wireType === 5) {
      if (position + 4 > end) {
        return { bytes: bytes.subarray(start, end), changed: false };
      }
      position += 4;
      chunks.push(bytes.subarray(fieldStart, position));
      continue;
    }

    return { bytes: bytes.subarray(start, end), changed: false };
  }

  if (!changed) {
    return { bytes: bytes.subarray(start, end), changed: false };
  }
  return { bytes: concatChunks(chunks), changed: true };
}

try {
  const response = typeof $response !== "undefined" ? $response : null;
  const useBodyBytes = response && response.bodyBytes != null;
  const endpoint = endpointName();
  const input = toBytes(
    response ? (useBodyBytes ? response.bodyBytes : response.body) : null
  );

  if (!input || input.length === 0) {
    $done({});
  } else if (endpoint === "guide") {
    const result = cleanGuideNavigation(input, 0, input.length);
    if (!result.changed) {
      console.log(
        "YouTube guide cleaner [" +
          SCRIPT_VERSION +
          "]: size=" +
          input.length +
          " blocked-item-not-found"
      );
      $done({});
    } else if (useBodyBytes) {
      console.log(
        "YouTube guide cleaner [" +
          SCRIPT_VERSION +
          "]: size=" +
          input.length +
          " removed=" +
          (input.length - result.bytes.length)
      );
      $done({ bodyBytes: result.bytes });
    } else {
      console.log(
        "YouTube guide cleaner [" +
          SCRIPT_VERSION +
          "]: size=" +
          input.length +
          " removed=" +
          (input.length - result.bytes.length)
      );
      $done({ body: result.bytes });
    }
  } else if (!hasAdMarker(input, 0, input.length)) {
    console.log(
      "YouTube feed cleaner [" +
        SCRIPT_VERSION +
        "]: endpoint=" +
        endpoint +
        " size=" +
        input.length +
        " no-ad-marker"
    );
    $done({});
  } else {
    const result = cleanMessage(input, 0, input.length, 0);
    if (!result.changed) {
      console.log(
        "YouTube feed cleaner [" +
          SCRIPT_VERSION +
          "]: endpoint=" +
          endpoint +
          " size=" +
          input.length +
          " marker-found-but-not-removed"
      );
      $done({});
    } else if (useBodyBytes) {
      console.log(
        "YouTube feed cleaner [" +
          SCRIPT_VERSION +
          "]: endpoint=" +
          endpoint +
          " size=" +
          input.length +
          " removed=" +
          (input.length - result.bytes.length)
      );
      $done({ bodyBytes: result.bytes });
    } else {
      console.log(
        "YouTube feed cleaner [" +
          SCRIPT_VERSION +
          "]: endpoint=" +
          endpoint +
          " size=" +
          input.length +
          " removed=" +
          (input.length - result.bytes.length)
      );
      $done({ body: result.bytes });
    }
  }
} catch (error) {
  console.log(
    "YouTube feed ad cleanup [" + SCRIPT_VERSION + "] failed: " + String(error)
  );
  $done({});
}
