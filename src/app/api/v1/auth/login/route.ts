import { NextRequest } from 'next/server';
import { webAuthnService } from '@/services/webauthn.service';
import { jsonOk, jsonFail } from '@/lib/utils/response';
import { ApiCode } from '@/lib/utils/status';
import { AppError } from '@/lib/utils/error';

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { address, authentication, challengeToken } = body;

    let result;
    if (address) {
      // Info: (20260105 - Tzuhan) 原有邏輯
      result = await webAuthnService.loginWithAddress(address, authentication);
    } else if (challengeToken) {
      // Info: (20260105 - Tzuhan) [New] 無地址登入
      result = await webAuthnService.loginWithCredential(challengeToken, authentication);
    } else {
      throw new AppError(ApiCode.VALIDATION_ERROR, 'Missing login parameters');
    }

    // Info: (20251223 - Tzuhan) result 包含 { dewt, user }
    return jsonOk(result);
  } catch (error) {
    console.error('[API] Login error:', error);
    if (error instanceof AppError) {
      return jsonFail(error.code, error.message);
    }
    return jsonFail(ApiCode.UNAUTHORIZED, 'Login failed');
  }
}
