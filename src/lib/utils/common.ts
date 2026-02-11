export function randomPassword(length: number = 32): string {
  const chars =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let result = "";
  for (let i = 0; i < length; i++) {
    result += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return result;
}

// Info: (20260206 - Julian) 將時間戳轉換為日期/時間字串的工具
export function timestampToString(timestamp: number | undefined) {
  if (timestamp === 0 || timestamp === undefined || timestamp === null) {
    return {
      dateWithSlash: "-",
      dateWithDash: "-",
      dateAndTime: "-",
    };
  }

  const dateObj = new Date(timestamp * 1000);

  // Info: (20260206 - Julian) 取出年份
  const year = dateObj.getFullYear();

  // Info: (20260206 - Julian) 取出月份
  const month = dateObj.getMonth() + 1;
  const monthWithPad = month.toString().padStart(2, "0"); // Info: (20260206 - Julian) 二位數月份

  // Info: (20260206 - Julian) 取出日期
  const day = dateObj.getDate();
  const dayWithPad = day.toString().padStart(2, "0"); // Info: (20260206 - Julian) 二位數日期

  // Info: (20260206 - Julian) 取出時間
  const hours = dateObj.getHours();
  const hoursWithPad = hours.toString().padStart(2, "0"); // Info: (20260206 - Julian) 二位數小時
  const minutes = dateObj.getMinutes();
  const minutesWithPad = minutes.toString().padStart(2, "0"); // Info: (20260206 - Julian) 二位數分鐘

  // Info: (20260206 - Julian) Formatting
  const dateWithSlash = `${year}/${monthWithPad}/${dayWithPad}`;
  const dateWithDash = `${year}-${monthWithPad}-${dayWithPad}`;
  const dateAndTime = `${year}-${monthWithPad}-${dayWithPad} ${hoursWithPad}:${minutesWithPad}`;

  return {
    dateWithSlash, // Info: (20260206 - Julian) e.g., "2026/01/01"
    dateWithDash, // Info: (20260206 - Julian) e.g., "2026-01-01"
    dateAndTime, // Info: (20260206 - Julian) e.g., "2026-01-01 12:34"
  };
}
