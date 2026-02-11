import { encodeAbiParameters, parseAbiParameters, type Hex } from 'viem';
import { AuthenticationJSON } from '@passwordless-id/webauthn/dist/esm/types';

// Info: (20251230 - Tzuhan) 瀏覽器端 Hex 轉 Base64URL
export function hexToBase64Url(hex: string): string {
  const hexStr = hex.startsWith('0x') ? hex.slice(2) : hex;
  const match = hexStr.match(/.{1,2}/g);
  if (!match) return '';
  const bytes = new Uint8Array(match.map((byte) => parseInt(byte, 16)));
  let binary = '';
  bytes.forEach((b) => (binary += String.fromCharCode(b)));
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

// Info: (20251230 - Tzuhan) Base64 轉 Hex (含 Padding 修正)
export function base64ToHex(base64: string): string {
  let padded = base64.replace(/-/g, '+').replace(/_/g, '/');
  while (padded.length % 4) {
    padded += '=';
  }

  const binary = atob(padded);
  let hex = '';
  for (let i = 0; i < binary.length; i++) {
    hex += binary.charCodeAt(i).toString(16).padStart(2, '0');
  }
  return hex;
}

// Info: (20251230 - Tzuhan) 僅回傳結構物件，不編碼
export function getWebAuthnSignatureStruct(
  authentication: AuthenticationJSON,
  pubKeyX: bigint,
  pubKeyY: bigint
) {
  const { authenticatorData, clientDataJSON, signature } = authentication.response;

  // Info: (20251230 - Tzuhan) 1. 解析 DER 簽名 (提取 r, s)
  const sigHex = base64ToHex(signature);
  let p = 0;

  if (sigHex.substring(p, p + 2) !== '30') throw new Error('Invalid DER signature header');
  p += 2;

  const lenByte = parseInt(sigHex.substring(p, p + 2), 16);
  p += 2;
  if (lenByte & 0x80) {
    const lenBytesCount = lenByte & 0x7f;
    p += lenBytesCount * 2;
  }

  if (sigHex.substring(p, p + 2) !== '02') throw new Error('Invalid DER r tag');
  p += 2;
  const rLen = parseInt(sigHex.substring(p, p + 2), 16);
  p += 2;
  const rHex = sigHex.substring(p, p + rLen * 2);
  const r = BigInt('0x' + rHex);
  p += rLen * 2;

  if (sigHex.substring(p, p + 2) !== '02') throw new Error('Invalid DER s tag');
  p += 2;
  const sLen = parseInt(sigHex.substring(p, p + 2), 16);
  p += 2;
  const sHex = sigHex.substring(p, p + sLen * 2);
  const s = BigInt('0x' + sHex);
  p += sLen * 2;

  // Info: (20251230 - Tzuhan) 2. 定位 challenge 和 type
  let clientDataBase64 = clientDataJSON.replace(/-/g, '+').replace(/_/g, '/');
  while (clientDataBase64.length % 4) {
    clientDataBase64 += '=';
  }
  const clientDataStr = atob(clientDataBase64);

  const challengePos = clientDataStr.indexOf('"challenge"');
  const challengeValStart = clientDataStr.indexOf('"', challengePos + 11) + 1;
  const typePos = clientDataStr.indexOf('"type"');
  const typeValStart = clientDataStr.indexOf('"', typePos + 6) + 1;

  // Info: (20251230 - Tzuhan) 回傳物件
  return {
    authenticatorData: `0x${base64ToHex(authenticatorData)}` as Hex,
    clientDataJSON: `0x${base64ToHex(clientDataJSON)}` as Hex,
    challengeLocation: BigInt(challengeValStart),
    responseTypeLocation: BigInt(typeValStart),
    r,
    s,
    pubKeyX,
    pubKeyY,
  };
}

// Info: (20251230 - Tzuhan) [Wrapper] 用於 PersonalSCW (單一結構編碼)
export function encodeWebAuthnSignature(
  authentication: AuthenticationJSON,
  pubKeyX: bigint,
  pubKeyY: bigint
): Hex {
  const struct = getWebAuthnSignatureStruct(authentication, pubKeyX, pubKeyY);
  return encodeAbiParameters(
    parseAbiParameters(
      '(bytes authenticatorData, bytes clientDataJSON, uint256 challengeLocation, uint256 responseTypeLocation, uint256 r, uint256 s, uint256 pubKeyX, uint256 pubKeyY)'
    ),
    [struct]
  );
}

export function extractXYFromSPKI(spkiBase64: string) {
  // Info: (20251205 - Tzuhan) 1. 處理 Base64URL 格式 (將 - 轉為 +, _ 轉為 /): WebAuthn 輸出的通常是 Base64URL，但 Node.js 的 Buffer 容錯率高，為了保險起見，我們標準化它。
  const base64 = spkiBase64.replace(/-/g, '+').replace(/_/g, '/');

  // Info: (20251205 - Tzuhan) 2. 解碼為 Buffer
  const buffer = Buffer.from(base64, 'base64');

  /**
   * Info: (20251205 - Tzuhan) 3. 定位公鑰位置
   * P-256 的 SPKI Header 固定為 26 bytes。
   * 第 27 byte (index 26) 通常是 0x04 (代表未壓縮的座標點 format)
   * 檢查標頭長度與格式標記 (0x04)
   * 直接找最後的 65 bytes (1 byte prefix + 32 byte X + 32 byte Y)
   */
  const keyLength = 65;
  const start = buffer.length - keyLength;

  if (buffer[start] !== 0x04) {
    throw new Error('Public key is not in uncompressed format (0x04)');
  }

  // Info: (20251205 - Tzuhan) 4. 切割 X 和 Y，跳過 0x04，取接下來的 32 bytes 為 X，再接下來 32 bytes 為 Y
  const xBuffer = buffer.subarray(start + 1, start + 1 + 32);
  const yBuffer = buffer.subarray(start + 1 + 32, start + 1 + 32 + 32);

  return {
    x: BigInt('0x' + xBuffer.toString('hex')),
    y: BigInt('0x' + yBuffer.toString('hex')),
  };
}

/**
 * Info: (20251226 - Tzuhan) 將 X, Y 座標還原為 P-256 SPKI (DER) 格式
 */
export function reconstructKeyFromXY(xStr: string, yStr: string): string {
  // Info: (20251226 - Tzuhan) P-256 SPKI Header (ASN.1 DER sequence for id-ecPublicKey + prime256v1)
  // Info: (20251226 - Tzuhan) Hex: 3059301306072a8648ce3d020106082a8648ce3d030107034200
  const SPKI_HEADER = Buffer.from('3059301306072a8648ce3d020106082a8648ce3d030107034200', 'hex');

  const toBuffer32 = (numStr: string) => {
    let hex = BigInt(numStr).toString(16);
    if (hex.length % 2 !== 0) hex = '0' + hex;
    const buf = Buffer.from(hex, 'hex');
    const padded = Buffer.alloc(32);
    buf.copy(padded, 32 - buf.length);
    return padded;
  };

  const x = toBuffer32(xStr);
  const y = toBuffer32(yStr);

  // Info: (20251226 - Tzuhan) 0x04 表示 Uncompressed Point
  const uncompressedPoint = Buffer.concat([Buffer.from([0x04]), x, y]);

  // Info: (20251226 - Tzuhan) 組合 Header + Point
  return Buffer.concat([SPKI_HEADER, uncompressedPoint]).toString('base64url');
}
