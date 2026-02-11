import { z } from 'zod';
import { ApiCode } from '@/lib/utils/status';

/** Info: (20250822 - Tzuhan) 數字都以字串傳輸，避免 JS 浮點誤差 */
export const decimalStringSchema = z.string().regex(/^-?\d+(\.\d+)?$/, 'Decimal string expected');
export const bigIntStringSchema = z.string().regex(/^\d+$/, 'BigInt string expected');

/** Info: (20250822 - Tzuhan) 任意 JSON 值（不解析） */
type JSONPrimitive = string | number | boolean | null;
interface IJSONObject {
  [key: string]: JSONValue;
}
type JSONArray = JSONValue[];
export type JSONValue = JSONPrimitive | IJSONObject | JSONArray;
const jsonPrimitiveSchema = z.union([z.string(), z.number(), z.boolean(), z.null()]);
export const jsonValueSchema: z.ZodType<JSONValue> = z.lazy(() =>
  z.union([jsonPrimitiveSchema, z.array(jsonValueSchema), z.record(z.string(), jsonValueSchema)])
);

/* Info: (20250822 - Tzuhan) ========== 排序 ========== */
export const sortOrderSchema = z.enum(['asc', 'desc']);
export const sortSpecSchema = z
  .array(z.object({ sortBy: z.string(), sortOrder: sortOrderSchema }))
  .readonly()
  .optional();

/* Info: (20250822 - Tzuhan) ========== Query Helpers ========== */

/** Info: (20250822 - Tzuhan) 多值字串參數：支援 ?x=a&x=b 與 ?x=a,b；自動 trim/去重；限制最多 20 個 */
export const stringArrayParamSchema = z
  .union([z.string().min(1), z.array(z.string().min(1))])
  .transform((v) => (Array.isArray(v) ? v : v.split(',').map((s) => s.trim())))
  .pipe(
    z
      .array(z.string().min(1))
      .max(20) // Info: (20250822 - Tzuhan) 限制最多 20 個
      .transform((arr) => Array.from(new Set(arr))) // Info: (20250822 - Tzuhan) 去重
  )
  .optional();

/** Info: (20250822 - Tzuhan) 日期輸入（Query 用）：接受 YYYY-MM-DD 或 ISO 字串 → 轉成 Date 物件 */
export const dateParamSchema = z
  .string()
  .transform((s) => new Date(s))
  .refine((d) => !Number.isNaN(d.getTime()), 'Invalid date format (expect YYYY-MM-DD or ISO)');

/** Info: (20250822 - Tzuhan) 僅日期字串（輸出或需要嚴格 YYYY-MM-DD 的場合） */
export const dateYMDParamSchema = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, 'Must be YYYY-MM-DD')
  .refine((s) => {
    const d = new Date(`${s}T00:00:00Z`);
    return !Number.isNaN(d.valueOf()) && d.toISOString().slice(0, 10) === s;
  }, 'Invalid date');

/** Info: (20251002 - Tzuhan) 完整 ISO 8601 日期時間字串 (輸出用) */
export const isoDateTimeStringSchema = z
  .string()
  .regex(
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/,
    'Must be ISO-8601 datetime (e.g., 2025-08-14T02:34:56Z)'
  );

/** Info: (20250822 - Tzuhan) 分頁 Query（default 只有在值為 undefined 時生效；請在 route 用 `?? undefined` 餵值） */
export const pageQuerySchema = z.object({
  page: z.coerce.number().int().min(1, 'page must be >= 1').default(1),
  pageSize: z.coerce
    .number()
    .int()
    .min(1, 'pageSize must be >= 1')
    .max(100, 'pageSize too large')
    .default(20),
});
export type PageQuery = z.infer<typeof pageQuerySchema>;

/* Info: (20250822 - Tzuhan) ========== 分頁容器（payload 用） ========== */
export const paginationSchema = z.object({
  page: z.number().int().positive(),
  pageSize: z.number().int().positive(),
  total: z.number().int().nonnegative(),
  pages: z.number().int().positive(),
  hasNext: z.boolean(),
  hasPrev: z.boolean(),
  sort: sortSpecSchema.optional(),
  note: z.string().optional(),
});

/* Info: (20250822 - Tzuhan) ========== 統一回應包裝 ========== */
export const apiResponseSchema = <T extends z.ZodTypeAny>(payload: T) =>
  z.object({
    powerby: z.string(),
    success: z.boolean(),
    code: z.enum(Object.values(ApiCode) as [string, ...string[]]),
    message: z.string(),
    payload,
  });

export const paginatedOfSchema = <T extends z.ZodTypeAny>(item: T) =>
  z.object({ items: z.array(item).readonly() }).and(paginationSchema);
