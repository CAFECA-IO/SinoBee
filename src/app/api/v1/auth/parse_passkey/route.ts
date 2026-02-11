import { NextRequest } from 'next/server';
import { webAuthnService } from '@/services/webauthn.service';
import { jsonOk, jsonFail } from '@/lib/utils/response';
import { ApiCode } from '@/lib/utils/status';
import { AppError } from '@/lib/utils/error';

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { registration, challenge } = body;

    if (!registration || !challenge) {
      throw new AppError(ApiCode.VALIDATION_ERROR, 'Registration data and challenge are required');
    }

    // Info: (20251223 - Tzuhan) 呼叫 Service 解析 Passkey，取得 x, y 座標與 Credential ID
    const result = await webAuthnService.parseRegistrationCredential(registration, challenge);

    return jsonOk(result);
  } catch (error) {
    console.error('[API] Parse Passkey Error:', error);

    if (error instanceof AppError) {
      return jsonFail(error.code, error.message);
    }

    return jsonFail(ApiCode.VALIDATION_ERROR, 'Failed to parse passkey credential');
  }
}
