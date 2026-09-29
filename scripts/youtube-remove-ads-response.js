/*
 * YouTube feed ad cleanup for Stash/Loon.
 *
 * Newer YouTube iOS responses keep feed ads in protobuf field 50195462.
 * Remove only that field when its payload contains a reliable ad marker so
 * ordinary recommendations remain untouched.
 */

const AD_ITEM_FIELD = 50195462;
const MAX_DEPTH = 32;
const AD_MARKERS = ["pagead", "AD_CPN", "[VIEWABILITY]"].map(toAsciiBytes);

function toAsciiBytes(value) {
  const bytes = new Uint8Array(value.length);
  for (let index = 0; index < value.length; index += 1) {
    bytes[index] = value.charCodeAt(index);
  }
  return bytes;
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

function cleanMessage(bytes, start, end, depth) {
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
        fieldNumber === AD_ITEM_FIELD &&
        hasAdMarker(bytes, payloadStart, payloadEnd)
      ) {
        changed = true;
        continue;
      }

      if (depth < MAX_DEPTH) {
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
  const input = toBytes(
    response ? (useBodyBytes ? response.bodyBytes : response.body) : null
  );

  if (!input || input.length === 0) {
    $done({});
  } else {
    const result = cleanMessage(input, 0, input.length, 0);
    if (!result.changed) {
      $done({});
    } else if (useBodyBytes) {
      $done({ bodyBytes: result.bytes });
    } else {
      $done({ body: result.bytes });
    }
  }
} catch (error) {
  console.log("YouTube remove ads failed: " + String(error));
  $done({});
}
