import fs from "node:fs";

const cmdFile = "/app/napcat/plugins/qqcmd.json";
const resFile = "/app/napcat/plugins/qqcmd_result.json";
let lastSeq = 0;
let timer = null;

function svc(ctx) {
  const s = ctx.core && ctx.core.context && ctx.core.context.session;
  return s && s.getBuddyService ? s.getBuddyService() : null;
}

async function handle(ctx, cmd) {
  if (cmd.action.startsWith("qzone_")) {
    const web = ctx.core.apis.WebApi;
    if (cmd.action === "qzone_auth") return await web.getQzoneAuth();
    if (cmd.action === "qzone_publish") {
      const richvals = Array.isArray(cmd.richvals) ? cmd.richvals.slice() : [];
      for (const b64 of cmd.images || []) {
        try {
          const rv = await web.uploadImageToQzone(String(b64));
          if (rv) richvals.push(rv);
        } catch (e) {
          ctx.logger.error("[qqcmd] qzone upload failed: " + ((e && e.message) || e));
        }
      }
      return await web.publishQzoneMsg(
        String(cmd.content || ""),
        richvals,
        Number(cmd.ugc_right || 64),
        cmd.target_uins || []
      );
    }
    if (cmd.action === "qzone_delete") return await web.deleteQzoneMsg(String(cmd.tid));
  }
  if (cmd.action === "add_custom_face") {
    const b64 = String(cmd.b64 || "").replace(/^data:[^;]+;base64,/, "");
    const file = "/tmp/napcat-face-" + Date.now() + ".jpg";
    fs.writeFileSync(file, Buffer.from(b64, "base64"));
    const r = await ctx.actions.call(
      "add_custom_face",
      { file, is_origin: true },
      ctx.adapterName,
      ctx.pluginManager.config
    );
    return r;
  }
  if (cmd.action === "profile_set") {
    return await ctx.core.apis.UserApi.setQQProfile(cmd.payload || {});
  }
  if (cmd.action === "avatar_set") {
    return await ctx.core.apis.UserApi.setQQAvatar(String(cmd.file || ""));
  }
  const b = svc(ctx);
  if (!b) throw new Error("buddy service not ready");
  if (cmd.action === "raw_add") return await b.reqToAddFriends(cmd.payload || {});
  if (cmd.action === "add_friend") return await b.reqToAddFriends({ uid: String(cmd.uid), message: String(cmd.message || ""), sourceId: "" });
  if (cmd.action === "list_requests") return await b.getBuddyReq();
  if (cmd.action === "approve") return await b.approvalFriendRequest({ friendUid: String(cmd.uid), reqTime: String(cmd.reqTime), accept: true });
  throw new Error("unknown action " + cmd.action);
}

export const plugin_init = async (ctx) => {
  try {
    const prev = JSON.parse(fs.readFileSync(resFile, "utf-8"));
    if (prev && prev.seq) lastSeq = prev.seq;
  } catch (e) {}

  timer = setInterval(async () => {
    try {
      if (!fs.existsSync(cmdFile)) return;
      const cmd = JSON.parse(fs.readFileSync(cmdFile, "utf-8"));
      if (!cmd || !cmd.seq || cmd.seq === lastSeq) return;
      lastSeq = cmd.seq;
      const out = { seq: cmd.seq, action: cmd.action, ok: true, at: Date.now() };
      try {
        out.result = await handle(ctx, cmd);
      } catch (e) {
        out.ok = false;
        out.error = String((e && e.message) || e);
      }
      fs.writeFileSync(resFile, JSON.stringify(out, null, 2));
      ctx.logger.log("[qqcmd] " + JSON.stringify(out).slice(0, 300));
    } catch (e) {
      ctx.logger.error("[qqcmd] loop " + e);
    }
  }, 1200);
};

export const plugin_cleanup = () => {
  if (timer) clearInterval(timer);
};
