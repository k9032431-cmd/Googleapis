// =====================================================================
//  Cloudflare Worker — прокси подписки PasarGuard
//  Сохраняет ВСЕ заголовки панели (profile-title, subscription-userinfo,
//  announce и т.д.), поэтому вид подписки в клиенте — как в оригинале:
//  название, трафик, срок, баннер. Данные живые (не статика).
//
//  Клиент обращается к Worker (адрес на Cloudflare), Worker забирает
//  подписку с панели по её домену/IP и возвращает ответ как есть.
// =====================================================================

// Origin — адрес панели, откуда брать подписку.
// subs.spacevpntm.online в Cloudflare стоит как "DNS only" (серое облако),
// поэтому fetch уходит напрямую на твой сервер 37.233.80.52.
const ORIGIN = "https://subs.spacevpntm.online";

export default {
  async fetch(request) {
    const url = new URL(request.url);

    // Отбрасываем служебный путь /favicon.ico и пустой корень.
    if (url.pathname === "/" || url.pathname === "/favicon.ico") {
      return new Response("ok", { status: 200 });
    }

    // Тот же путь и query, что пришёл клиенту (/sub/<token>, /<token>/<type> и т.п.)
    const target = ORIGIN + url.pathname + url.search;

    let resp;
    try {
      resp = await fetch(target, {
        method: request.method,
        headers: {
          // User-Agent важен: по нему панель выбирает формат (v2ray/clash/…)
          "User-Agent": request.headers.get("User-Agent") || "",
          "Accept": request.headers.get("Accept") || "*/*",
          "Accept-Language": request.headers.get("Accept-Language") || "",
        },
        redirect: "follow",
      });
    } catch (e) {
      return new Response("upstream error: " + e, { status: 502 });
    }

    // Возвращаем тело и ВСЕ заголовки панели без изменений — именно они
    // дают название/трафик/срок/баннер в клиенте.
    const headers = new Headers(resp.headers);
    headers.set("Access-Control-Allow-Origin", "*");

    return new Response(resp.body, {
      status: resp.status,
      statusText: resp.statusText,
      headers,
    });
  },
};
