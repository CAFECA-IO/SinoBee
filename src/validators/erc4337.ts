import { z } from 'zod';
import { apiResponseSchema } from '@/validators/common';

// Info: (20251118 - Tzuhan) Zod helper to validate 0x-prefixed hex strings
const hexStringSchema = z.string().regex(/^0x[0-9a-fA-F]*$/, {
  message: 'Not a valid 0x-prefixed hex string',
});

// Info: (20251118 - Tzuhan) 1. Zod schema for the JSON transport layer (API 傳輸用)
// Info: (20251118 - Tzuhan) 這裡所有的 BigInt 都被表示為 0x-prefixed hex strings
export const userOperationJsonSchema = z.object({
  sender: hexStringSchema.regex(/^0x[0-9a-fA-F]{40}$/, 'Invalid sender address'),
  nonce: hexStringSchema,
  initCode: hexStringSchema,
  callData: hexStringSchema,
  callGasLimit: hexStringSchema,
  verificationGasLimit: hexStringSchema,
  preVerificationGas: hexStringSchema,
  maxFeePerGas: hexStringSchema,
  maxPriorityFeePerGas: hexStringSchema,
  paymasterAndData: hexStringSchema,
  signature: hexStringSchema,
});

// Info: (20251118 - Tzuhan) 2. TypeScript type inferred from the JSON schema
export type UserOperationJson = z.infer<typeof userOperationJsonSchema>;

// Info: (20251118 - Tzuhan) 3. Zod schema for internal use (程式內部使用)
// Info: (20251118 - Tzuhan) 這個 schema 會自動將 0x-prefixed hex strings 轉換為 BigInt
const hexToBigInt = z.string().transform((val, ctx) => {
  try {
    // Info: (20251118 - Tzuhan) BigInt(val) 可以處理 '0x...' 格式
    return BigInt(val);
  } catch {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'Invalid hex string, cannot convert to BigInt',
    });
    return z.NEVER;
  }
});

export const userOperationSchema = userOperationJsonSchema.extend({
  nonce: hexToBigInt,
  callGasLimit: hexToBigInt,
  verificationGasLimit: hexToBigInt,
  preVerificationGas: hexToBigInt,
  maxFeePerGas: hexToBigInt,
  maxPriorityFeePerGas: hexToBigInt,
});

// Info: (20251118 - Tzuhan) 4. TypeScript type inferred from the BigInt schema (內部函式用)
export type UserOperation = z.infer<typeof userOperationSchema>;

// Info: (20251118 - Tzuhan) 5. [可選] 為 Bundler 的回應定義型別
export const bundlerResponsePayloadSchema = z.object({
  transactionHash: hexStringSchema.optional(),
  status: z.string().optional(),
  error: z.string().optional(),
  details: z.string().optional(),
  message: z.string().optional(),
});

export const bundlerResponseSchema = apiResponseSchema(bundlerResponsePayloadSchema);
export type BundlerResponse = z.infer<typeof bundlerResponseSchema>;
