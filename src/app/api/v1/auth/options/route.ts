import { NextRequest } from 'next/server';
import { webAuthnService } from '@/services/webauthn.service';
import { jsonOk, jsonFail } from '@/lib/utils/response';
import { ApiCode } from '@/lib/utils/status';
import { AppError } from '@/lib/utils/error';
import { randomBytes } from 'crypto';

/**
 * Info: (20260116 - Tzuhan) 統一 WebAuthn 選項/挑戰碼入口
 * GET /api/v1/auth/options?action=register
 * GET /api/v1/auth/options?action=login&address=0x... (有狀態登入)
 * GET /api/v1/auth/options?action=login (無狀態/探索式登入)
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const action = searchParams.get('action');
    const address = searchParams.get('address');

    // Info: (20260116 - Tzuhan) 1. 處理註冊挑戰碼
    if (action === 'register') {
      const challenge = randomBytes(32).toString('base64url');
      return jsonOk({ challenge });
    }

    // Info: (20260116 - Tzuhan) 2. 處理登入挑戰碼
    if (action === 'login') {
      if (address) {
        // Info: (20260116 - Tzuhan) 有地址 -> 查鏈同步 -> 存入 DB
        const challenge = await webAuthnService.generateLoginOptions(address);
        return jsonOk({ challenge });
      } else {
        // Info: (20260116 - Tzuhan) 無地址 -> Stateless Challenge (返回 challenge + token)
        const { challenge, token } = await webAuthnService.generateStatelessLoginOptions();
        return jsonOk({ challenge, token });
      }
    }

    throw new AppError(ApiCode.VALIDATION_ERROR, 'Invalid action parameter');
  } catch (error) {
    console.error('[API] Options generation error:', error);
    if (error instanceof AppError) {
      return jsonFail(error.code, error.message);
    }
    return jsonFail(ApiCode.INTERNAL_SERVER_ERROR, 'Failed to generate auth options');
  }
}
