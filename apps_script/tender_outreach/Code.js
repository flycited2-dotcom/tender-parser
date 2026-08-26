/*
 * Tender outreach mailer for Google Apps Script.
 *
 * Test drafts, working drafts and production sends use separate explicit
 * switches. Every real recipient is rechecked against campaign approval,
 * per-row approval, consent evidence, the global stop-list and daily limits.
 */

var TenderOutreach = (function () {
  "use strict";

  var CONFIG = {
    spreadsheetId: "1Dos070wEj_zwymVn46i4mcC4fWMLpDsZVmC7KkX8HSQ",
    sheets: {
      queue: "Очередь",
      template: "Шаблон",
      campaigns: "Кампании",
      stoplist: "Стоп-лист",
      events: "События",
      dmarc: "DMARC",
    },
    properties: {
      testMode: "TENDER_OUTREACH_TEST_MODE",
      workingDraftsMode: "TENDER_OUTREACH_WORKING_DRAFTS_MODE",
      productionMode: "TENDER_OUTREACH_PRODUCTION_MODE",
      schedulerMode: "TENDER_OUTREACH_SCHEDULER_MODE",
      autoPreparationMode: "TENDER_OUTREACH_AUTO_PREPARATION_MODE",
      firstBatchReviewed: "TENDER_OUTREACH_FIRST_BATCH_REVIEWED",
      mailboxMonitorMode: "TENDER_OUTREACH_MAILBOX_MONITOR_MODE",
      dmarcMonitorMode: "TENDER_OUTREACH_DMARC_MONITOR_MODE",
      processedDmarcIds: "TENDER_OUTREACH_PROCESSED_DMARC_IDS",
      processedMailboxIds: "TENDER_OUTREACH_PROCESSED_MAILBOX_IDS",
      productionBatchLimit: "TENDER_OUTREACH_BATCH_LIMIT",
      testRecipient: "TENDER_OUTREACH_TEST_RECIPIENT",
      senderAlias: "TENDER_OUTREACH_FROM_ALIAS",
      senderName: "TENDER_OUTREACH_SENDER_NAME",
      replyTo: "TENDER_OUTREACH_REPLY_TO",
    },
    queue: {
      decisionReady: "ready_for_campaign_review",
      statusQueued: "в очереди",
      statusTestDraftCreating: "создание тестового черновика",
      statusTestDraft: "тестовый черновик",
      statusTestDraftError: "ошибка тестового черновика",
      statusWorkingDraftCreating: "создание рабочего черновика",
      statusWorkingDraft: "рабочий черновик",
      statusWorkingDraftError: "ошибка рабочего черновика",
      stageWorkingDraft: "рабочий черновик",
      statusSending: "отправляется",
      statusSent: "отправлено",
      statusSendError: "ошибка отправки",
      stageSent: "отправлено",
      statusBounced: "bounced",
      stageBounced: "недоставка",
      statusOptedOut: "не писать",
      stageOptedOut: "отписка",
    },
    campaign: {
      testStatus: "тест",
      approvalPhrase: "TEST_DRAFTS_APPROVED",
      workingDraftStatus: "одобрена",
      workingDraftApprovalPhrase: "WORK_DRAFTS_APPROVED",
      productionStatus: "одобрена",
      productionApprovalPhrase: "PRODUCTION_SEND_APPROVED",
      ownerAuthorizedTenderPhrase: "PUBLIC_TENDER_OUTREACH_AUTHORIZED",
    },
    blockedSubjectPrefixes: ["[ЗАБЛОКИРОВАНО]", "[ЧЕРНОВИК"],
    maxHardTestDraftsPerRun: 5,
    maxHardWorkingDraftsPerRun: 20,
    maxHardSendsPerRun: 10,
    maxHardSendsPerDay: 50,
    defaultProductionBatchLimit: 5,
    maxProcessedMessageIds: 300,
    mailboxLookbackDays: 14,
  };

  var QUEUE_HEADERS = {
    candidateId: "ID кандидата",
    campaignId: "ID кампании",
    email: "Email",
    organization: "Организация",
    region: "Регион",
    contactPerson: "Контактное лицо",
    contactSource: "Источник контакта",
    sourceTender: "Закупка-основание",
    decision: "Решение",
    decisionReason: "Причина решения",
    mailingStatus: "Статус рассылки",
    sentAt: "Дата отправки",
    stage: "Этап",
    draftId: "ID черновика",
    messageId: "ID сообщения",
    threadId: "ID цепочки",
    note: "Заметка",
    approved: "Одобрено",
    autoSend: "Автоотправка",
    contactBasis: "Основание обращения",
    consentStatus: "Статус согласия",
    consentEvidence: "Подтверждение согласия",
    consentDate: "Дата согласия",
  };

  var CAMPAIGN_HEADERS = {
    id: "ID кампании",
    status: "Статус",
    dailyLimit: "Лимит/день",
    runLimit: "Лимит/запуск",
    sendWindow: "Окно отправки",
    days: "Дни",
    approved: "Одобрено владельцем",
    approvedAt: "Дата одобрения",
    comment: "Комментарий",
  };

  function normalizeEmail(value) {
    var text = String(value || "").trim().toLowerCase();
    var match = text.match(/[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}/i);
    return match ? match[0].replace(/\.$/, "") : "";
  }

  function asBoolean(value) {
    return value === true || String(value || "").trim().toLowerCase() === "true";
  }

  function normalizeLabel(value) {
    return String(value || "").trim().toLowerCase().replace(/\s+/g, " ");
  }

  function indexHeaders(headers) {
    var result = {};
    headers.forEach(function (header, index) {
      var key = String(header || "").trim();
      if (key) result[key] = index;
    });
    return result;
  }

  function requireHeaders(index, required, tableName) {
    Object.keys(required).forEach(function (key) {
      var header = required[key];
      if (typeof index[header] !== "number") {
        throw new Error("В листе «" + tableName + "» нет столбца «" + header + "»");
      }
    });
  }

  function isBlockedTemplate(template) {
    var subject = String((template && template.subject) || "").trim();
    var body = String((template && template.body) || "").trim();
    return (
      !subject ||
      !body ||
      CONFIG.blockedSubjectPrefixes.some(function (prefix) {
        return subject.indexOf(prefix) === 0;
      })
    );
  }

  function renderTemplate(text, candidate) {
    var values = {
      "{{Организация}}": candidate.organization || "",
      "{{Регион}}": candidate.region || "",
      "{{Контактное лицо}}": candidate.contactPerson || "",
      "{{Email}}": candidate.email || "",
    };
    return Object.keys(values).reduce(function (rendered, placeholder) {
      return rendered.split(placeholder).join(String(values[placeholder]));
    }, String(text || ""));
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function plainTextToHtml(text) {
    var escaped = escapeHtml(text);
    var linked = escaped.replace(/https?:\/\/[^\s<]+/g, function (url) {
      return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + url + "</a>";
    });
    var emphasized = linked
      .replace(/ООО «Технолайн Трейд»/g, "<strong>ООО «Технолайн Трейд»</strong>")
      .replace(
        /Направьте нам запрос, техническое задание или спецификацию ответным письмом/g,
        "<strong>Направьте нам запрос, техническое задание или спецификацию ответным письмом</strong>"
      );
    return '<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.5;color:#202124">' +
      emphasized.replace(/\r?\n/g, "<br>") +
      "</div>";
  }

  function sanitizeHeader(value) {
    return String(value || "").replace(/[\r\n]+/g, " ").trim();
  }

  function encodeMimeHeader(value) {
    var safe = sanitizeHeader(value);
    return "=?UTF-8?B?" +
      Utilities.base64Encode(safe, Utilities.Charset.UTF_8) +
      "?=";
  }

  function base64MimeBody(value) {
    var encoded = Utilities.base64Encode(
      String(value || ""),
      Utilities.Charset.UTF_8
    );
    return (encoded.match(/.{1,76}/g) || [""]).join("\r\n");
  }

  function buildRawDraftMessage(to, subject, plainBody, options) {
    var boundary = "tender-outreach-" + Utilities.getUuid();
    var fromEmail = normalizeEmail(options.from);
    var fromHeader = fromEmail;
    if (options.name) fromHeader = encodeMimeHeader(options.name) + " <" + fromEmail + ">";
    var headers = [
      "From: " + fromHeader,
      "To: " + normalizeEmail(to),
      "Subject: " + encodeMimeHeader(subject),
      "MIME-Version: 1.0",
      'Content-Type: multipart/alternative; boundary="' + boundary + '"',
    ];
    if (options.replyTo) headers.splice(2, 0, "Reply-To: " + normalizeEmail(options.replyTo));

    var raw = headers.join("\r\n") + "\r\n\r\n" +
      "--" + boundary + "\r\n" +
      "Content-Type: text/plain; charset=UTF-8\r\n" +
      "Content-Transfer-Encoding: base64\r\n\r\n" +
      base64MimeBody(plainBody) + "\r\n" +
      "--" + boundary + "\r\n" +
      "Content-Type: text/html; charset=UTF-8\r\n" +
      "Content-Transfer-Encoding: base64\r\n\r\n" +
      base64MimeBody(plainTextToHtml(plainBody)) + "\r\n" +
      "--" + boundary + "--";

    return Utilities.base64EncodeWebSafe(raw, Utilities.Charset.UTF_8).replace(/=+$/, "");
  }

  function createDraftViaGmailApi(to, subject, plainBody, options) {
    return Gmail.Users.Drafts.create(
      { message: { raw: buildRawDraftMessage(to, subject, plainBody, options) } },
      "me"
    );
  }

  function updateDraftViaGmailApi(draftId, to, subject, plainBody, options) {
    return Gmail.Users.Drafts.update(
      { message: { raw: buildRawDraftMessage(to, subject, plainBody, options) } },
      "me",
      String(draftId || "")
    );
  }

  function hashEmail(email) {
    var normalized = normalizeEmail(email);
    if (!normalized) return "";
    return Utilities.computeDigest(
      Utilities.DigestAlgorithm.SHA_256,
      normalized,
      Utilities.Charset.UTF_8
    ).map(function (byte) {
      var unsigned = byte < 0 ? byte + 256 : byte;
      return ("0" + unsigned.toString(16)).slice(-2);
    }).join("");
  }

  function buildEventRow(
    timestamp,
    eventId,
    campaignId,
    candidateId,
    emailHash,
    type,
    oldStatus,
    newStatus,
    initiator,
    details
  ) {
    return [
      timestamp,
      eventId,
      campaignId,
      candidateId,
      emailHash,
      type,
      oldStatus,
      newStatus,
      initiator,
      details || "",
    ];
  }

  function validateCampaignForTest(campaign) {
    if (!campaign) return "campaign_not_found";
    if (normalizeLabel(campaign.status) !== CONFIG.campaign.testStatus) {
      return "campaign_not_in_test_status";
    }
    if (!asBoolean(campaign.approved)) return "campaign_not_approved";
    if (!String(campaign.approvedAt || "").trim()) return "campaign_approval_date_missing";
    if (String(campaign.comment || "").indexOf(CONFIG.campaign.approvalPhrase) < 0) {
      return "campaign_test_approval_phrase_missing";
    }
    return "";
  }

  function validateCampaignForWorkingDrafts(campaign) {
    if (!campaign) return "campaign_not_found";
    if (normalizeLabel(campaign.status) !== CONFIG.campaign.workingDraftStatus) {
      return "campaign_not_approved_for_working_drafts";
    }
    if (!asBoolean(campaign.approved)) return "campaign_not_approved";
    if (!String(campaign.approvedAt || "").trim()) return "campaign_approval_date_missing";
    if (
      String(campaign.comment || "").indexOf(
        CONFIG.campaign.workingDraftApprovalPhrase
      ) < 0
    ) {
      return "campaign_working_draft_approval_phrase_missing";
    }
    return "";
  }

  function validateCampaignForProduction(campaign) {
    if (!campaign) return "campaign_not_found";
    if (normalizeLabel(campaign.status) !== CONFIG.campaign.productionStatus) {
      return "campaign_not_approved_for_production";
    }
    if (!asBoolean(campaign.approved)) return "campaign_not_approved";
    if (!String(campaign.approvedAt || "").trim()) return "campaign_approval_date_missing";
    if (
      String(campaign.comment || "").indexOf(
        CONFIG.campaign.productionApprovalPhrase
      ) < 0
    ) {
      return "campaign_production_approval_phrase_missing";
    }
    return "";
  }

  function candidateFromRow(row, index) {
    return {
      candidateId: String(row[index[QUEUE_HEADERS.candidateId]] || "").trim(),
      campaignId: String(row[index[QUEUE_HEADERS.campaignId]] || "").trim(),
      email: normalizeEmail(row[index[QUEUE_HEADERS.email]]),
      organization: String(row[index[QUEUE_HEADERS.organization]] || "").trim(),
      region: String(row[index[QUEUE_HEADERS.region]] || "").trim(),
      contactPerson: String(row[index[QUEUE_HEADERS.contactPerson]] || "").trim(),
      contactSource: String(row[index[QUEUE_HEADERS.contactSource]] || "").trim(),
      sourceTender: String(row[index[QUEUE_HEADERS.sourceTender]] || "").trim(),
      decision: String(row[index[QUEUE_HEADERS.decision]] || "").trim(),
      decisionReason: String(row[index[QUEUE_HEADERS.decisionReason]] || "").trim(),
      mailingStatus: String(row[index[QUEUE_HEADERS.mailingStatus]] || "").trim(),
      sentAt: row[index[QUEUE_HEADERS.sentAt]],
      stage: String(row[index[QUEUE_HEADERS.stage]] || "").trim(),
      draftId: String(row[index[QUEUE_HEADERS.draftId]] || "").trim(),
      messageId: String(row[index[QUEUE_HEADERS.messageId]] || "").trim(),
      threadId: String(row[index[QUEUE_HEADERS.threadId]] || "").trim(),
      approved: asBoolean(row[index[QUEUE_HEADERS.approved]]),
      autoSend: asBoolean(row[index[QUEUE_HEADERS.autoSend]]),
      contactBasis: normalizeLabel(row[index[QUEUE_HEADERS.contactBasis]]),
      consentStatus: normalizeLabel(row[index[QUEUE_HEADERS.consentStatus]]),
      consentEvidence: String(row[index[QUEUE_HEADERS.consentEvidence]] || "").trim(),
      consentDate: String(row[index[QUEUE_HEADERS.consentDate]] || "").trim(),
    };
  }

  function candidateEligibilityAfterCampaign(
    candidate,
    campaign,
    stopEmails,
    template,
    campaignReason,
    draftedEmails
  ) {
    if (campaignReason) return campaignReason;
    if (isBlockedTemplate(template)) return "template_blocked";
    if (!candidate.candidateId) return "candidate_id_missing";
    if (!candidate.email) return "email_missing_or_invalid";
    if (candidate.campaignId !== campaign.id) return "campaign_mismatch";
    if (candidate.decision !== CONFIG.queue.decisionReady) return "decision_not_ready";
    if (normalizeLabel(candidate.mailingStatus) !== CONFIG.queue.statusQueued) {
      return "mailing_status_not_queued";
    }
    if (!candidate.approved) return "candidate_not_approved";
    if (candidate.autoSend) return "auto_send_must_remain_false_in_test";
    if (candidate.draftId) return "draft_already_exists";
    if (draftedEmails && draftedEmails[candidate.email]) {
      return "email_already_has_draft";
    }
    if (stopEmails && stopEmails[candidate.email]) return "globally_suppressed";
    if (candidate.consentStatus !== "подтверждено") return "consent_not_confirmed";
    if (!hasConfirmedContactBasis(candidate)) {
      return "contact_basis_not_permitted";
    }
    if (!candidate.consentEvidence) return "consent_evidence_missing";
    if (!candidate.consentDate) return "consent_date_missing";
    return "";
  }

  function hasConfirmedContactBasis(candidate) {
    return ["согласие получено", "входящий запрос", "действующий договор"].indexOf(
      candidate.contactBasis
    ) >= 0;
  }

  function isHttpUrl(value) {
    return /^https?:\/\/[^\s]+$/i.test(String(value || "").trim());
  }

  function campaignAuthorizesPublicTenderOutreach(campaign) {
    return Boolean(
      campaign &&
      String(campaign.comment || "").indexOf(
        CONFIG.campaign.ownerAuthorizedTenderPhrase
      ) >= 0
    );
  }

  function hasPublicTenderEvidence(candidate) {
    return Boolean(
      candidate &&
      candidate.organization &&
      candidate.email &&
      isHttpUrl(candidate.sourceTender) &&
      String(candidate.contactSource || "").trim()
    );
  }

  function hasPermittedOutreachBasis(candidate, campaign) {
    var confirmedConsent = Boolean(
      candidate &&
      candidate.consentStatus === "подтверждено" &&
      hasConfirmedContactBasis(candidate) &&
      candidate.consentEvidence &&
      candidate.consentDate
    );
    return confirmedConsent || Boolean(
      campaignAuthorizesPublicTenderOutreach(campaign) &&
      hasPublicTenderEvidence(candidate)
    );
  }

  function automatedDraftEligibility(
    candidate,
    campaign,
    stopEmails,
    template,
    draftedEmails
  ) {
    var campaignReason = validateCampaignForWorkingDrafts(campaign) ||
      validateCampaignForProduction(campaign);
    if (campaignReason) return campaignReason;
    if (!campaignAuthorizesPublicTenderOutreach(campaign)) {
      return "public_tender_outreach_not_authorized";
    }
    if (isBlockedTemplate(template)) return "template_blocked";
    if (!candidate.candidateId) return "candidate_id_missing";
    if (!candidate.email) return "email_missing_or_invalid";
    if (candidate.campaignId !== campaign.id) return "campaign_mismatch";
    if (["needs_contact_review", CONFIG.queue.decisionReady].indexOf(candidate.decision) < 0) {
      return "decision_not_eligible_for_automation";
    }
    if (
      ["заблокировано", CONFIG.queue.statusQueued].indexOf(
        normalizeLabel(candidate.mailingStatus)
      ) < 0
    ) {
      return "mailing_status_not_available_for_preparation";
    }
    if (candidate.messageId || candidate.sentAt) return "already_sent";
    if (candidate.draftId) return "draft_already_exists";
    if (draftedEmails && draftedEmails[candidate.email]) {
      return "email_already_has_draft";
    }
    if (stopEmails && stopEmails[candidate.email]) return "globally_suppressed";
    if (!hasPublicTenderEvidence(candidate)) return "public_tender_evidence_missing";
    return "";
  }

  function candidateEligibility(candidate, campaign, stopEmails, template) {
    return candidateEligibilityAfterCampaign(
      candidate,
      campaign,
      stopEmails,
      template,
      validateCampaignForTest(campaign),
      null
    );
  }

  function workingDraftEligibility(
    candidate,
    campaign,
    stopEmails,
    template,
    draftedEmails
  ) {
    return candidateEligibilityAfterCampaign(
      candidate,
      campaign,
      stopEmails,
      template,
      validateCampaignForWorkingDrafts(campaign),
      draftedEmails || null
    );
  }

  function productionSendEligibility(candidate, campaign, stopEmails, template) {
    var campaignReason = validateCampaignForProduction(campaign);
    if (campaignReason) return campaignReason;
    if (isBlockedTemplate(template)) return "template_blocked";
    if (!candidate.candidateId) return "candidate_id_missing";
    if (!candidate.email) return "email_missing_or_invalid";
    if (candidate.campaignId !== campaign.id) return "campaign_mismatch";
    if (candidate.decision !== CONFIG.queue.decisionReady) return "decision_not_ready";
    if (normalizeLabel(candidate.mailingStatus) !== CONFIG.queue.statusWorkingDraft) {
      return "working_draft_status_required";
    }
    if (normalizeLabel(candidate.stage) !== CONFIG.queue.stageWorkingDraft) {
      return "working_draft_stage_required";
    }
    if (!candidate.approved) return "candidate_not_approved";
    if (!candidate.autoSend) return "auto_send_not_approved";
    if (!candidate.draftId) return "draft_missing";
    if (candidate.messageId || candidate.sentAt) return "already_sent";
    if (stopEmails && stopEmails[candidate.email]) return "globally_suppressed";
    if (!hasPermittedOutreachBasis(candidate, campaign)) {
      return "outreach_basis_not_permitted";
    }
    return "";
  }

  function getSpreadsheet() {
    var spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
    if (!spreadsheet || spreadsheet.getId() !== CONFIG.spreadsheetId) {
      throw new Error("Скрипт запущен не из утверждённой тендерной таблицы");
    }
    return spreadsheet;
  }

  function requireSheet(spreadsheet, name) {
    var sheet = spreadsheet.getSheetByName(name);
    if (!sheet) throw new Error("Не найден обязательный лист «" + name + "»");
    return sheet;
  }

  function readTable(sheet) {
    var lastRow = sheet.getLastRow();
    var lastColumn = sheet.getLastColumn();
    if (lastRow < 1 || lastColumn < 1) return { headers: [], rows: [], index: {} };
    var values = sheet.getRange(1, 1, lastRow, lastColumn).getValues();
    var headers = values[0];
    return { headers: headers, rows: values.slice(1), index: indexHeaders(headers) };
  }

  function readTemplate(sheet) {
    var values = sheet.getRange(1, 1, Math.max(sheet.getLastRow(), 1), 2).getValues();
    var keyed = {};
    values.forEach(function (row) {
      keyed[String(row[0] || "").trim()] = row[1];
    });
    return { subject: keyed["Тема"] || "", body: keyed["Тело"] || "" };
  }

  function readCampaigns(sheet) {
    var table = readTable(sheet);
    requireHeaders(table.index, CAMPAIGN_HEADERS, CONFIG.sheets.campaigns);
    var campaigns = {};
    table.rows.forEach(function (row) {
      var id = String(row[table.index[CAMPAIGN_HEADERS.id]] || "").trim();
      if (!id) return;
      campaigns[id] = {
        id: id,
        status: row[table.index[CAMPAIGN_HEADERS.status]],
        dailyLimit: Number(row[table.index[CAMPAIGN_HEADERS.dailyLimit]]) || 0,
        runLimit: Number(row[table.index[CAMPAIGN_HEADERS.runLimit]]) || 0,
        sendWindow: String(row[table.index[CAMPAIGN_HEADERS.sendWindow]] || "").trim(),
        days: String(row[table.index[CAMPAIGN_HEADERS.days]] || "").trim(),
        approved: row[table.index[CAMPAIGN_HEADERS.approved]],
        approvedAt: row[table.index[CAMPAIGN_HEADERS.approvedAt]],
        comment: row[table.index[CAMPAIGN_HEADERS.comment]],
      };
    });
    return campaigns;
  }

  function readStopEmails(sheet) {
    var lastRow = sheet.getLastRow();
    if (lastRow < 2) return {};
    var values = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
    return values.reduce(function (result, row) {
      var email = normalizeEmail(row[0]);
      if (email) result[email] = true;
      return result;
    }, {});
  }

  function loadContext() {
    var spreadsheet = getSpreadsheet();
    var queueSheet = requireSheet(spreadsheet, CONFIG.sheets.queue);
    var queue = readTable(queueSheet);
    requireHeaders(queue.index, QUEUE_HEADERS, CONFIG.sheets.queue);
    return {
      spreadsheet: spreadsheet,
      queueSheet: queueSheet,
      queue: queue,
      campaigns: readCampaigns(requireSheet(spreadsheet, CONFIG.sheets.campaigns)),
      template: readTemplate(requireSheet(spreadsheet, CONFIG.sheets.template)),
      stopEmails: readStopEmails(requireSheet(spreadsheet, CONFIG.sheets.stoplist)),
      eventsSheet: requireSheet(spreadsheet, CONFIG.sheets.events),
    };
  }

  function previewQueue() {
    var context = loadContext();
    var summary = { total: 0, eligibleForTestDraft: 0, blocked: {} };
    context.queue.rows.forEach(function (row) {
      if (!row.some(function (value) { return String(value || "").trim(); })) return;
      summary.total += 1;
      var candidate = candidateFromRow(row, context.queue.index);
      var reason = candidateEligibility(
        candidate,
        context.campaigns[candidate.campaignId],
        context.stopEmails,
        context.template
      );
      if (!reason) summary.eligibleForTestDraft += 1;
      else summary.blocked[reason] = (summary.blocked[reason] || 0) + 1;
    });
    Logger.log(JSON.stringify(summary));
    return summary;
  }

  function existingDraftEmails(rows, index) {
    return rows.reduce(function (result, row) {
      var candidate = candidateFromRow(row, index);
      if (candidate.email && candidate.draftId) result[candidate.email] = true;
      return result;
    }, {});
  }

  function previewWorkingQueue() {
    var context = loadContext();
    var draftedEmails = existingDraftEmails(context.queue.rows, context.queue.index);
    var summary = { total: 0, eligibleForWorkingDraft: 0, blocked: {} };
    context.queue.rows.forEach(function (row) {
      if (!row.some(function (value) { return String(value || "").trim(); })) return;
      summary.total += 1;
      var candidate = candidateFromRow(row, context.queue.index);
      var reason = workingDraftEligibility(
        candidate,
        context.campaigns[candidate.campaignId],
        context.stopEmails,
        context.template,
        draftedEmails
      );
      if (!reason) {
        summary.eligibleForWorkingDraft += 1;
        draftedEmails[candidate.email] = true;
      } else {
        summary.blocked[reason] = (summary.blocked[reason] || 0) + 1;
      }
    });
    Logger.log(JSON.stringify(summary));
    return summary;
  }

  function requireSenderConfiguration(properties) {
    var senderAlias = normalizeEmail(properties.getProperty(CONFIG.properties.senderAlias));
    if (!senderAlias) throw new Error("Не задан обязательный Gmail-алиас отправителя");
    return {
      senderAlias: senderAlias,
      senderName: String(properties.getProperty(CONFIG.properties.senderName) || "").trim(),
      replyTo: normalizeEmail(properties.getProperty(CONFIG.properties.replyTo)),
    };
  }

  function requireTestConfiguration() {
    var properties = PropertiesService.getScriptProperties();
    if (properties.getProperty(CONFIG.properties.testMode) !== "true") {
      throw new Error("TEST_MODE не включён явно в свойствах скрипта");
    }
    var testRecipient = normalizeEmail(
      properties.getProperty(CONFIG.properties.testRecipient)
    );
    if (!testRecipient) throw new Error("Не задан корректный тестовый адресат");
    var sender = requireSenderConfiguration(properties);
    sender.testRecipient = testRecipient;
    return sender;
  }

  function requireWorkingDraftConfiguration() {
    var properties = PropertiesService.getScriptProperties();
    if (properties.getProperty(CONFIG.properties.workingDraftsMode) !== "true") {
      throw new Error("WORKING_DRAFTS_MODE не включён явно в свойствах скрипта");
    }
    return requireSenderConfiguration(properties);
  }

  function appendEvent(
    sheet,
    campaignId,
    candidateId,
    email,
    type,
    oldStatus,
    newStatus,
    details
  ) {
    var eventId = Utilities.getUuid();
    sheet.appendRow(buildEventRow(
      new Date(),
      eventId,
      campaignId,
      candidateId,
      hashEmail(email),
      type,
      oldStatus,
      newStatus,
      Session.getEffectiveUser().getEmail(),
      details
    ));
  }

  function createTestDrafts() {
    var runtime = requireTestConfiguration();
    var lock = LockService.getScriptLock();
    lock.waitLock(5000);
    try {
      var context = loadContext();
      var created = [];
      for (var rowOffset = 0; rowOffset < context.queue.rows.length; rowOffset += 1) {
        var row = context.queue.rows[rowOffset];
        var candidate = candidateFromRow(row, context.queue.index);
        var campaign = context.campaigns[candidate.campaignId];
        var reason = candidateEligibility(
          candidate,
          campaign,
          context.stopEmails,
          context.template
        );
        if (reason) continue;

        var limit = Math.min(
          Math.max(Number(campaign.runLimit) || 0, 0),
          CONFIG.maxHardTestDraftsPerRun
        );
        if (!limit || created.length >= limit) continue;

        var sheetRow = rowOffset + 2;
        var statusColumn = context.queue.index[QUEUE_HEADERS.mailingStatus] + 1;
        var stageColumn = context.queue.index[QUEUE_HEADERS.stage] + 1;
        var draftColumn = context.queue.index[QUEUE_HEADERS.draftId] + 1;
        var noteColumn = context.queue.index[QUEUE_HEADERS.note] + 1;
        var oldStatus = candidate.mailingStatus;

        context.queueSheet
          .getRange(sheetRow, statusColumn)
          .setValue(CONFIG.queue.statusTestDraftCreating);
        SpreadsheetApp.flush();

        try {
          var subject = renderTemplate(context.template.subject, candidate);
          var body = renderTemplate(context.template.body, candidate);
          var testSubject = "[TEST → " + candidate.email + "] " + subject;
          var testBody =
            "ТЕСТОВЫЙ ЧЕРНОВИК. Получатель подменён на контрольный адрес.\n" +
            "Исходный адресат: " + candidate.email + "\n" +
            "ID кандидата: " + candidate.candidateId + "\n\n" +
            body;
          var options = { from: runtime.senderAlias };
          if (runtime.senderName) options.name = runtime.senderName;
          if (runtime.replyTo) options.replyTo = runtime.replyTo;
          var draft = createDraftViaGmailApi(
            runtime.testRecipient,
            testSubject,
            testBody,
            options
          );
          var draftId = String((draft && draft.id) || "");
          if (!draftId) {
            throw new Error("Gmail API не вернул ID созданного черновика");
          }
          context.queueSheet.getRange(sheetRow, draftColumn).setValue(draftId);
          context.queueSheet.getRange(sheetRow, statusColumn).setValue(CONFIG.queue.statusTestDraft);
          context.queueSheet.getRange(sheetRow, stageColumn).setValue("тест");
          context.queueSheet
            .getRange(sheetRow, noteColumn)
            .setValue("Тестовый адресат подменён; отправка запрещена");
          appendEvent(
            context.eventsSheet,
            candidate.campaignId,
            candidate.candidateId,
            candidate.email,
            "test_draft_created",
            oldStatus,
            CONFIG.queue.statusTestDraft,
            "recipient_replaced_with_test_address"
          );
          created.push(candidate.candidateId);
        } catch (error) {
          context.queueSheet
            .getRange(sheetRow, statusColumn)
            .setValue(CONFIG.queue.statusTestDraftError);
          context.queueSheet.getRange(sheetRow, noteColumn).setValue(String(error.message || error));
          appendEvent(
            context.eventsSheet,
            candidate.campaignId,
            candidate.candidateId,
            candidate.email,
            "test_draft_failed",
            CONFIG.queue.statusTestDraftCreating,
            CONFIG.queue.statusTestDraftError,
            String(error.message || error)
          );
        }
      }
      return { created: created.length, candidateIds: created };
    } finally {
      lock.releaseLock();
    }
  }

  function createWorkingDrafts() {
    var runtime = requireWorkingDraftConfiguration();
    var lock = LockService.getScriptLock();
    lock.waitLock(5000);
    try {
      var context = loadContext();
      var created = [];
      var createdByCampaign = {};
      var draftedEmails = existingDraftEmails(context.queue.rows, context.queue.index);

      for (var rowOffset = 0; rowOffset < context.queue.rows.length; rowOffset += 1) {
        if (created.length >= CONFIG.maxHardWorkingDraftsPerRun) break;

        var row = context.queue.rows[rowOffset];
        var candidate = candidateFromRow(row, context.queue.index);
        var campaign = context.campaigns[candidate.campaignId];
        var reason = workingDraftEligibility(
          candidate,
          campaign,
          context.stopEmails,
          context.template,
          draftedEmails
        );
        if (reason) continue;

        var campaignLimit = Math.min(
          Math.max(Number(campaign.runLimit) || 0, 0),
          CONFIG.maxHardWorkingDraftsPerRun
        );
        var campaignCreated = createdByCampaign[candidate.campaignId] || 0;
        if (!campaignLimit || campaignCreated >= campaignLimit) continue;

        var sheetRow = rowOffset + 2;
        var statusColumn = context.queue.index[QUEUE_HEADERS.mailingStatus] + 1;
        var stageColumn = context.queue.index[QUEUE_HEADERS.stage] + 1;
        var draftColumn = context.queue.index[QUEUE_HEADERS.draftId] + 1;
        var noteColumn = context.queue.index[QUEUE_HEADERS.note] + 1;
        var oldStatus = candidate.mailingStatus;

        context.queueSheet
          .getRange(sheetRow, statusColumn)
          .setValue(CONFIG.queue.statusWorkingDraftCreating);
        SpreadsheetApp.flush();
        draftedEmails[candidate.email] = true;

        try {
          var subject = renderTemplate(context.template.subject, candidate);
          var body = renderTemplate(context.template.body, candidate);
          var options = { from: runtime.senderAlias };
          if (runtime.senderName) options.name = runtime.senderName;
          if (runtime.replyTo) options.replyTo = runtime.replyTo;

          var draft = createDraftViaGmailApi(
            candidate.email,
            subject,
            body,
            options
          );
          var draftId = String((draft && draft.id) || "");
          if (!draftId) {
            throw new Error("Gmail API не вернул ID созданного рабочего черновика");
          }

          context.queueSheet.getRange(sheetRow, draftColumn).setValue(draftId);
          context.queueSheet
            .getRange(sheetRow, statusColumn)
            .setValue(CONFIG.queue.statusWorkingDraft);
          context.queueSheet
            .getRange(sheetRow, stageColumn)
            .setValue(CONFIG.queue.stageWorkingDraft);
          context.queueSheet
            .getRange(sheetRow, noteColumn)
            .setValue("Рабочий черновик создан; автоматическая отправка запрещена");
          appendEvent(
            context.eventsSheet,
            candidate.campaignId,
            candidate.candidateId,
            candidate.email,
            "working_draft_created",
            oldStatus,
            CONFIG.queue.statusWorkingDraft,
            "draft_only_no_send"
          );
          created.push(candidate.candidateId);
          createdByCampaign[candidate.campaignId] = campaignCreated + 1;
        } catch (error) {
          context.queueSheet
            .getRange(sheetRow, statusColumn)
            .setValue(CONFIG.queue.statusWorkingDraftError);
          context.queueSheet.getRange(sheetRow, noteColumn).setValue(String(error.message || error));
          appendEvent(
            context.eventsSheet,
            candidate.campaignId,
            candidate.candidateId,
            candidate.email,
            "working_draft_failed",
            CONFIG.queue.statusWorkingDraftCreating,
            CONFIG.queue.statusWorkingDraftError,
            String(error.message || error)
          );
        }
      }

      return {
        created: created.length,
        candidateIds: created,
        hardLimit: CONFIG.maxHardWorkingDraftsPerRun,
      };
    } finally {
      lock.releaseLock();
    }
  }

  function requireAutomatedPreparationConfiguration() {
    var properties = PropertiesService.getScriptProperties();
    if (properties.getProperty(CONFIG.properties.autoPreparationMode) !== "true") {
      throw new Error("AUTO_PREPARATION_MODE не включён явно в свойствах скрипта");
    }
    return requireWorkingDraftConfiguration();
  }

  function existingPreparedByCampaign(rows, index) {
    return rows.reduce(function (result, row) {
      var candidate = candidateFromRow(row, index);
      if (candidate.campaignId && candidate.draftId && !candidate.messageId && !candidate.sentAt) {
        result[candidate.campaignId] = (result[candidate.campaignId] || 0) + 1;
      }
      return result;
    }, {});
  }

  function previewAutomatedPreparation() {
    var context = loadContext();
    var draftedEmails = existingDraftEmails(context.queue.rows, context.queue.index);
    var summary = { total: 0, eligibleForAutomatedDraft: 0, blocked: {} };
    context.queue.rows.forEach(function (row) {
      if (!row.some(function (value) { return String(value || "").trim(); })) return;
      summary.total += 1;
      var candidate = candidateFromRow(row, context.queue.index);
      var reason = automatedDraftEligibility(
        candidate,
        context.campaigns[candidate.campaignId],
        context.stopEmails,
        context.template,
        draftedEmails
      );
      if (!reason) {
        summary.eligibleForAutomatedDraft += 1;
        draftedEmails[candidate.email] = true;
      } else {
        summary.blocked[reason] = (summary.blocked[reason] || 0) + 1;
      }
    });
    Logger.log(JSON.stringify(summary));
    return summary;
  }

  function prepareAutomatedWorkingDrafts() {
    var runtime = requireAutomatedPreparationConfiguration();
    var lock = LockService.getScriptLock();
    lock.waitLock(5000);
    try {
      var context = loadContext();
      var created = [];
      var preparedByCampaign = existingPreparedByCampaign(
        context.queue.rows,
        context.queue.index
      );
      var draftedEmails = existingDraftEmails(context.queue.rows, context.queue.index);

      for (var rowOffset = 0; rowOffset < context.queue.rows.length; rowOffset += 1) {
        if (created.length >= CONFIG.maxHardWorkingDraftsPerRun) break;
        var row = context.queue.rows[rowOffset];
        var candidate = candidateFromRow(row, context.queue.index);
        var campaign = context.campaigns[candidate.campaignId];
        var reason = automatedDraftEligibility(
          candidate,
          campaign,
          context.stopEmails,
          context.template,
          draftedEmails
        );
        if (reason) continue;

        var target = Math.min(
          Math.max(Number(campaign.dailyLimit) || 0, 0),
          CONFIG.maxHardWorkingDraftsPerRun
        );
        if (!target || (preparedByCampaign[candidate.campaignId] || 0) >= target) {
          continue;
        }

        var sheetRow = rowOffset + 2;
        var decisionColumn = context.queue.index[QUEUE_HEADERS.decision] + 1;
        var reasonColumn = context.queue.index[QUEUE_HEADERS.decisionReason] + 1;
        var statusColumn = context.queue.index[QUEUE_HEADERS.mailingStatus] + 1;
        var stageColumn = context.queue.index[QUEUE_HEADERS.stage] + 1;
        var draftColumn = context.queue.index[QUEUE_HEADERS.draftId] + 1;
        var noteColumn = context.queue.index[QUEUE_HEADERS.note] + 1;
        var approvedColumn = context.queue.index[QUEUE_HEADERS.approved] + 1;
        var autoSendColumn = context.queue.index[QUEUE_HEADERS.autoSend] + 1;
        var oldStatus = candidate.mailingStatus;

        context.queueSheet.getRange(sheetRow, decisionColumn, 1, 2).setValues([[
          CONFIG.queue.decisionReady,
          "owner_authorized_public_tender_outreach",
        ]]);
        context.queueSheet.getRange(sheetRow, statusColumn).setValue(
          CONFIG.queue.statusWorkingDraftCreating
        );
        context.queueSheet.getRange(sheetRow, approvedColumn, 1, 2).setValues([[true, true]]);
        SpreadsheetApp.flush();
        draftedEmails[candidate.email] = true;

        try {
          var subject = renderTemplate(context.template.subject, candidate);
          var body = renderTemplate(context.template.body, candidate);
          var options = { from: runtime.senderAlias };
          if (runtime.senderName) options.name = runtime.senderName;
          if (runtime.replyTo) options.replyTo = runtime.replyTo;
          var draft = createDraftViaGmailApi(candidate.email, subject, body, options);
          var draftId = String((draft && draft.id) || "");
          if (!draftId) {
            throw new Error("Gmail API не вернул ID автоматического черновика");
          }

          context.queueSheet.getRange(sheetRow, draftColumn).setValue(draftId);
          context.queueSheet.getRange(sheetRow, statusColumn).setValue(
            CONFIG.queue.statusWorkingDraft
          );
          context.queueSheet.getRange(sheetRow, stageColumn).setValue(
            CONFIG.queue.stageWorkingDraft
          );
          context.queueSheet.getRange(sheetRow, noteColumn).setValue(
            "Автоматически подготовлено по публичному контакту закупки; " +
            "отправка ожидает разовой проверки первой партии"
          );
          appendEvent(
            context.eventsSheet,
            candidate.campaignId,
            candidate.candidateId,
            candidate.email,
            "automated_working_draft_created",
            oldStatus,
            CONFIG.queue.statusWorkingDraft,
            "source_tender=" + candidate.sourceTender
          );
          created.push(candidate.candidateId);
          preparedByCampaign[candidate.campaignId] =
            (preparedByCampaign[candidate.campaignId] || 0) + 1;
        } catch (error) {
          context.queueSheet.getRange(sheetRow, statusColumn).setValue(
            CONFIG.queue.statusWorkingDraftError
          );
          context.queueSheet.getRange(sheetRow, autoSendColumn).setValue(false);
          context.queueSheet.getRange(sheetRow, noteColumn).setValue(
            String(error.message || error)
          );
          appendEvent(
            context.eventsSheet,
            candidate.campaignId,
            candidate.candidateId,
            candidate.email,
            "automated_working_draft_failed",
            CONFIG.queue.statusWorkingDraftCreating,
            CONFIG.queue.statusWorkingDraftError,
            String(error.message || error)
          );
        }
      }

      return {
        created: created.length,
        candidateIds: created,
        targetPerCampaign: CONFIG.maxHardWorkingDraftsPerRun,
      };
    } finally {
      lock.releaseLock();
    }
  }

  function refreshPreparedWorkingDrafts() {
    var properties = PropertiesService.getScriptProperties();
    if (properties.getProperty(CONFIG.properties.firstBatchReviewed) === "true") {
      throw new Error("Нельзя обновлять подготовленные черновики после активации отправки");
    }
    var runtime = requireWorkingDraftConfiguration();
    var lock = LockService.getScriptLock();
    lock.waitLock(5000);
    try {
      var context = loadContext();
      var updated = [];
      var failed = [];
      var noteColumn = context.queue.index[QUEUE_HEADERS.note] + 1;

      for (var rowOffset = 0; rowOffset < context.queue.rows.length; rowOffset += 1) {
        if (updated.length >= CONFIG.maxHardWorkingDraftsPerRun) break;
        var candidate = candidateFromRow(context.queue.rows[rowOffset], context.queue.index);
        if (normalizeLabel(candidate.mailingStatus) !== CONFIG.queue.statusWorkingDraft) continue;
        if (!candidate.draftId || candidate.sentAt || candidate.messageId || candidate.threadId) continue;

        var campaign = context.campaigns[candidate.campaignId];
        var reason = productionSendEligibility(
          candidate,
          campaign,
          context.stopEmails,
          context.template
        );
        if (reason) continue;

        var subject = renderTemplate(context.template.subject, candidate);
        var body = renderTemplate(context.template.body, candidate);
        var options = { from: runtime.senderAlias };
        if (runtime.senderName) options.name = runtime.senderName;
        if (runtime.replyTo) options.replyTo = runtime.replyTo;

        try {
          var draft = updateDraftViaGmailApi(
            candidate.draftId,
            candidate.email,
            subject,
            body,
            options
          );
          if (!draft || String(draft.id || "") !== candidate.draftId) {
            throw new Error("Gmail API не подтвердил обновление черновика");
          }
          context.queueSheet.getRange(rowOffset + 2, noteColumn).setValue(
            "Черновик обновлён по финально утверждённому шаблону; отправка ожидает активации"
          );
          appendEvent(
            context.eventsSheet,
            candidate.campaignId,
            candidate.candidateId,
            candidate.email,
            "working_draft_refreshed",
            CONFIG.queue.statusWorkingDraft,
            CONFIG.queue.statusWorkingDraft,
            "draft_id=" + candidate.draftId
          );
          updated.push(candidate.candidateId);
        } catch (error) {
          failed.push({
            candidateId: candidate.candidateId,
            draftId: candidate.draftId,
            error: String(error.message || error),
          });
        }
      }

      return { updated: updated.length, candidateIds: updated, failed: failed };
    } finally {
      lock.releaseLock();
    }
  }

  function clampProductionBatchLimit(value) {
    var parsed = Math.floor(Number(value));
    if (!parsed || parsed < 1) parsed = CONFIG.defaultProductionBatchLimit;
    return Math.min(parsed, CONFIG.maxHardSendsPerRun);
  }

  function parseClockMinutes(value) {
    var match = String(value || "").match(/(\d{1,2}):(\d{2})/);
    if (!match) return null;
    var hours = Number(match[1]);
    var minutes = Number(match[2]);
    if (hours > 23 || minutes > 59) return null;
    return hours * 60 + minutes;
  }

  function isAllowedWeekday(days, weekday) {
    var label = normalizeLabel(days)
      .replace(/ё/g, "е")
      .replace(/[—−-]/g, "–");
    if (!label || label === "ежедневно" || label === "каждый день") return true;
    if (label === "пн–пт" || label === "будни" || label === "по будням") {
      return weekday >= 1 && weekday <= 5;
    }
    var names = { пн: 1, вт: 2, ср: 3, чт: 4, пт: 5, сб: 6, вс: 7 };
    var allowed = {};
    label.split(/[,;\s]+/).forEach(function (token) {
      if (names[token]) allowed[names[token]] = true;
    });
    return Boolean(allowed[weekday]);
  }

  function isScheduleOpen(campaign, weekday, minuteOfDay) {
    if (!isAllowedWeekday(campaign.days, weekday)) return false;
    var matches = String(campaign.sendWindow || "").match(/\d{1,2}:\d{2}/g) || [];
    if (matches.length < 2) return true;
    var start = parseClockMinutes(matches[0]);
    var end = parseClockMinutes(matches[1]);
    if (start === null || end === null) return false;
    if (start <= end) return minuteOfDay >= start && minuteOfDay <= end;
    return minuteOfDay >= start || minuteOfDay <= end;
  }

  function currentScheduleState(now, timeZone) {
    return {
      weekday: Number(Utilities.formatDate(now, timeZone, "u")),
      minuteOfDay:
        Number(Utilities.formatDate(now, timeZone, "HH")) * 60 +
        Number(Utilities.formatDate(now, timeZone, "mm")),
    };
  }

  function dateKey(value, timeZone) {
    if (!value) return "";
    var date = value instanceof Date ? value : new Date(value);
    if (isNaN(date.getTime())) return "";
    return Utilities.formatDate(date, timeZone, "yyyy-MM-dd");
  }

  function sentTodayByCampaign(rows, index, now, timeZone) {
    var today = dateKey(now, timeZone);
    return rows.reduce(function (result, row) {
      var candidate = candidateFromRow(row, index);
      if (
        candidate.campaignId &&
        candidate.messageId &&
        dateKey(candidate.sentAt, timeZone) === today
      ) {
        result[candidate.campaignId] = (result[candidate.campaignId] || 0) + 1;
      }
      return result;
    }, {});
  }

  function previewProductionQueue() {
    var context = loadContext();
    var summary = { total: 0, eligibleForSend: 0, blocked: {} };
    context.queue.rows.forEach(function (row) {
      if (!row.some(function (value) { return String(value || "").trim(); })) return;
      summary.total += 1;
      var candidate = candidateFromRow(row, context.queue.index);
      var reason = productionSendEligibility(
        candidate,
        context.campaigns[candidate.campaignId],
        context.stopEmails,
        context.template
      );
      if (!reason) summary.eligibleForSend += 1;
      else summary.blocked[reason] = (summary.blocked[reason] || 0) + 1;
    });
    Logger.log(JSON.stringify(summary));
    return summary;
  }

  function requireProductionConfiguration(properties) {
    if (properties.getProperty(CONFIG.properties.productionMode) !== "true") {
      throw new Error("PRODUCTION_MODE не включён явно в свойствах скрипта");
    }
    var sender = requireSenderConfiguration(properties);
    sender.batchLimit = clampProductionBatchLimit(
      properties.getProperty(CONFIG.properties.productionBatchLimit)
    );
    return sender;
  }

  function sendDraftViaGmailApi(draftId) {
    return Gmail.Users.Drafts.send({ id: String(draftId || "") }, "me");
  }

  function sendProductionBatch(scheduled) {
    var properties = PropertiesService.getScriptProperties();
    if (
      scheduled &&
      properties.getProperty(CONFIG.properties.schedulerMode) !== "true"
    ) {
      return { sent: 0, skipped: "scheduler_disabled" };
    }
    if (properties.getProperty(CONFIG.properties.firstBatchReviewed) !== "true") {
      return { sent: 0, skipped: "first_batch_review_pending" };
    }

    var runtime = requireProductionConfiguration(properties);
    var lock = LockService.getScriptLock();
    lock.waitLock(5000);
    try {
      var context = loadContext();
      var now = new Date();
      var timeZone =
        context.spreadsheet.getSpreadsheetTimeZone() || Session.getScriptTimeZone();
      var schedule = currentScheduleState(now, timeZone);
      var sentToday = sentTodayByCampaign(
        context.queue.rows,
        context.queue.index,
        now,
        timeZone
      );
      var sentThisRun = {};
      var sentCandidateIds = [];

      var statusColumn = context.queue.index[QUEUE_HEADERS.mailingStatus] + 1;
      var sentAtColumn = context.queue.index[QUEUE_HEADERS.sentAt] + 1;
      var stageColumn = context.queue.index[QUEUE_HEADERS.stage] + 1;
      var draftColumn = context.queue.index[QUEUE_HEADERS.draftId] + 1;
      var messageColumn = context.queue.index[QUEUE_HEADERS.messageId] + 1;
      var threadColumn = context.queue.index[QUEUE_HEADERS.threadId] + 1;
      var noteColumn = context.queue.index[QUEUE_HEADERS.note] + 1;
      if (
        sentAtColumn !== statusColumn + 1 ||
        stageColumn !== statusColumn + 2 ||
        draftColumn !== statusColumn + 3 ||
        messageColumn !== statusColumn + 4 ||
        threadColumn !== statusColumn + 5 ||
        noteColumn !== statusColumn + 6
      ) {
        throw new Error("Столбцы результата отправки имеют неожиданное расположение");
      }

      for (var rowOffset = 0; rowOffset < context.queue.rows.length; rowOffset += 1) {
        if (sentCandidateIds.length >= runtime.batchLimit) break;

        var row = context.queue.rows[rowOffset];
        var candidate = candidateFromRow(row, context.queue.index);
        var campaign = context.campaigns[candidate.campaignId];
        var reason = productionSendEligibility(
          candidate,
          campaign,
          context.stopEmails,
          context.template
        );
        if (reason) continue;
        if (
          scheduled &&
          !isScheduleOpen(campaign, schedule.weekday, schedule.minuteOfDay)
        ) {
          continue;
        }

        var runLimit = Math.min(
          Math.max(Number(campaign.runLimit) || 0, 0),
          runtime.batchLimit,
          CONFIG.maxHardSendsPerRun
        );
        var dailyLimit = Math.min(
          Math.max(Number(campaign.dailyLimit) || 0, 0),
          CONFIG.maxHardSendsPerDay
        );
        var campaignSentThisRun = sentThisRun[candidate.campaignId] || 0;
        var campaignSentToday = sentToday[candidate.campaignId] || 0;
        if (!runLimit || campaignSentThisRun >= runLimit) continue;
        if (!dailyLimit || campaignSentToday >= dailyLimit) continue;

        var sheetRow = rowOffset + 2;
        var oldStatus = candidate.mailingStatus;
        context.queueSheet
          .getRange(sheetRow, statusColumn)
          .setValue(CONFIG.queue.statusSending);
        SpreadsheetApp.flush();

        try {
          var sentMessage = sendDraftViaGmailApi(candidate.draftId);
          var messageId = String((sentMessage && sentMessage.id) || "");
          var threadId = String((sentMessage && sentMessage.threadId) || "");
          if (!messageId) throw new Error("Gmail API не вернул ID отправленного сообщения");

          context.queueSheet
            .getRange(sheetRow, statusColumn, 1, 7)
            .setValues([[
              CONFIG.queue.statusSent,
              now,
              CONFIG.queue.stageSent,
              candidate.draftId,
              messageId,
              threadId,
              "Отправлено через подтверждённую production-партию",
            ]]);
          appendEvent(
            context.eventsSheet,
            candidate.campaignId,
            candidate.candidateId,
            candidate.email,
            "production_email_sent",
            oldStatus,
            CONFIG.queue.statusSent,
            "message_id=" + messageId + ";thread_id=" + threadId
          );
          sentCandidateIds.push(candidate.candidateId);
          sentThisRun[candidate.campaignId] = campaignSentThisRun + 1;
          sentToday[candidate.campaignId] = campaignSentToday + 1;
        } catch (error) {
          context.queueSheet
            .getRange(sheetRow, statusColumn)
            .setValue(CONFIG.queue.statusSendError);
          context.queueSheet
            .getRange(sheetRow, noteColumn)
            .setValue(String(error.message || error));
          appendEvent(
            context.eventsSheet,
            candidate.campaignId,
            candidate.candidateId,
            candidate.email,
            "production_email_failed",
            CONFIG.queue.statusSending,
            CONFIG.queue.statusSendError,
            String(error.message || error)
          );
        }
      }

      return {
        sent: sentCandidateIds.length,
        candidateIds: sentCandidateIds,
        batchLimit: runtime.batchLimit,
        hardDailyLimit: CONFIG.maxHardSendsPerDay,
      };
    } finally {
      lock.releaseLock();
    }
  }

  function approveAutomationAfterFirstBatchReview() {
    var properties = PropertiesService.getScriptProperties();
    requireProductionConfiguration(properties);
    properties.setProperty(CONFIG.properties.firstBatchReviewed, "true");
    return {
      approved: true,
      approvedAt: new Date().toISOString(),
      schedulerMode: properties.getProperty(CONFIG.properties.schedulerMode),
    };
  }

  function processedMessageSet(properties, propertyName) {
    var raw = String(properties.getProperty(propertyName) || "");
    if (!raw) return {};
    try {
      return JSON.parse(raw).reduce(function (result, id) {
        result[String(id)] = true;
        return result;
      }, {});
    } catch (error) {
      return {};
    }
  }

  function saveProcessedMessageSet(properties, propertyName, values) {
    var ids = Object.keys(values).slice(-CONFIG.maxProcessedMessageIds);
    properties.setProperty(propertyName, JSON.stringify(ids));
  }

  function gmailHeader(payload, name) {
    var target = String(name || "").toLowerCase();
    var headers = (payload && payload.headers) || [];
    for (var index = 0; index < headers.length; index += 1) {
      if (String(headers[index].name || "").toLowerCase() === target) {
        return String(headers[index].value || "");
      }
    }
    return "";
  }

  function decodeGmailText(data) {
    if (!data) return "";
    try {
      return Utilities.newBlob(Utilities.base64DecodeWebSafe(data))
        .getDataAsString("UTF-8");
    } catch (error) {
      return "";
    }
  }

  function collectGmailText(part, result) {
    if (!part) return;
    var mimeType = String(part.mimeType || "").toLowerCase();
    if ((mimeType === "text/plain" || mimeType === "text/html") && part.body) {
      result.push(decodeGmailText(part.body.data));
    }
    (part.parts || []).forEach(function (child) {
      collectGmailText(child, result);
    });
  }

  function gmailMessageText(message) {
    var parts = [];
    collectGmailText(message && message.payload, parts);
    return parts.join("\n");
  }

  function collectAttachmentBlobs(part, messageId, result) {
    if (!part) return;
    var filename = String(part.filename || "").trim();
    var body = part.body || {};
    if (filename && (body.attachmentId || body.data)) {
      var data = body.data;
      if (!data && body.attachmentId) {
        var attachment = Gmail.Users.Messages.Attachments.get(
          "me",
          messageId,
          body.attachmentId
        );
        data = attachment && attachment.data;
      }
      if (data && String(data).length <= 8 * 1024 * 1024) {
        result.push(Utilities.newBlob(
          Utilities.base64DecodeWebSafe(data),
          part.mimeType || "application/octet-stream",
          filename
        ));
      }
    }
    (part.parts || []).forEach(function (child) {
      collectAttachmentBlobs(child, messageId, result);
    });
  }

  function xmlTextsFromBlob(blob) {
    var name = String(blob.getName() || "").toLowerCase();
    var type = String(blob.getContentType() || "").toLowerCase();
    var blobs = [];
    try {
      if (/\.zip$/.test(name) || type.indexOf("zip") >= 0) {
        blobs = Utilities.unzip(blob);
      } else if (/\.gz$/.test(name) || type.indexOf("gzip") >= 0) {
        blobs = [Utilities.ungzip(blob)];
      } else {
        blobs = [blob];
      }
    } catch (error) {
      return [];
    }
    return blobs.reduce(function (result, item) {
      var itemName = String(item.getName() || name).toLowerCase();
      if (itemName && !/\.xml(?:\.txt)?$/.test(itemName) && itemName.indexOf("xml") < 0) {
        return result;
      }
      var bytes = item.getBytes();
      if (bytes.length > 4 * 1024 * 1024) return result;
      var text = item.getDataAsString("UTF-8").trim();
      if (text.indexOf("<feedback") >= 0 || text.indexOf(":feedback") >= 0) {
        result.push(text);
      }
      return result;
    }, []);
  }

  function xmlChild(element, name) {
    if (!element) return null;
    var children = element.getChildren();
    for (var index = 0; index < children.length; index += 1) {
      if (children[index].getName() === name) return children[index];
    }
    return null;
  }

  function xmlChildren(element, name) {
    if (!element) return [];
    return element.getChildren().filter(function (child) {
      return child.getName() === name;
    });
  }

  function xmlText(element, name) {
    var child = xmlChild(element, name);
    return child ? String(child.getText() || "").trim() : "";
  }

  function extractDmarcRowsFromXml(xmlText, messageId) {
    var root = XmlService.parse(String(xmlText || "")).getRootElement();
    if (root.getName() !== "feedback") return [];
    var metadata = xmlChild(root, "report_metadata");
    var policy = xmlChild(root, "policy_published");
    var reportId = xmlText(metadata, "report_id");
    var reporter = xmlText(metadata, "org_name");
    var domain = xmlText(policy, "domain");
    var dateRange = xmlChild(metadata, "date_range");
    var beginValue = Number(xmlText(dateRange, "begin"));
    var endValue = Number(xmlText(dateRange, "end"));
    var begin = beginValue ? new Date(beginValue * 1000) : "";
    var end = endValue ? new Date(endValue * 1000) : "";

    return xmlChildren(root, "record").map(function (record) {
      var row = xmlChild(record, "row");
      var evaluated = xmlChild(row, "policy_evaluated");
      var dkim = normalizeLabel(xmlText(evaluated, "dkim"));
      var spf = normalizeLabel(xmlText(evaluated, "spf"));
      return [
        new Date(),
        reporter,
        domain,
        reportId,
        begin,
        end,
        xmlText(row, "source_ip"),
        Number(xmlText(row, "count")) || 0,
        xmlText(evaluated, "disposition"),
        dkim,
        spf,
        dkim === "pass" || spf === "pass" ? "pass" : "fail",
        String(messageId || ""),
        "обработано",
      ];
    });
  }

  function ensureDmarcSheet(spreadsheet) {
    var sheet = spreadsheet.getSheetByName(CONFIG.sheets.dmarc);
    if (sheet) return sheet;
    sheet = spreadsheet.insertSheet(CONFIG.sheets.dmarc);
    var headers = [[
      "Обработано",
      "Источник отчёта",
      "Домен",
      "Report ID",
      "Период с",
      "Период по",
      "IP отправителя",
      "Писем",
      "Disposition",
      "DKIM",
      "SPF",
      "DMARC",
      "Gmail Message ID",
      "Статус",
    ]];
    sheet.getRange(1, 1, 1, headers[0].length).setValues(headers);
    sheet.setFrozenRows(1);
    sheet.setHiddenGridlines(true);
    sheet.getRange(1, 1, 1, headers[0].length)
      .setBackground("#1e518e")
      .setFontColor("#ffffff")
      .setFontWeight("bold")
      .setWrap(true);
    sheet.setColumnWidths(1, headers[0].length, 120);
    sheet.setColumnWidth(2, 180);
    sheet.setColumnWidth(3, 180);
    sheet.setColumnWidth(4, 220);
    return sheet;
  }

  function existingDmarcKeys(sheet) {
    if (sheet.getLastRow() < 2) return {};
    return sheet.getRange(2, 4, sheet.getLastRow() - 1, 5).getValues()
      .reduce(function (result, row) {
        var key = [row[0], row[1], row[2], row[3], row[4]].join("|");
        result[key] = true;
        return result;
      }, {});
  }

  function dmarcRowKey(row) {
    return [row[3], row[4], row[5], row[6], row[7]].join("|");
  }

  function listGmailMessageIds(query, maxResults) {
    var response = Gmail.Users.Messages.list("me", {
      q: query,
      maxResults: Math.min(Number(maxResults) || 100, 100),
    });
    return (response.messages || []).map(function (item) {
      return String(item.id || "");
    }).filter(Boolean);
  }

  function processDmarcReports() {
    var properties = PropertiesService.getScriptProperties();
    if (properties.getProperty(CONFIG.properties.dmarcMonitorMode) !== "true") {
      return { processed: 0, rows: 0, skipped: "dmarc_monitor_disabled" };
    }
    var context = loadContext();
    var sheet = ensureDmarcSheet(context.spreadsheet);
    var existing = existingDmarcKeys(sheet);
    var processed = processedMessageSet(properties, CONFIG.properties.processedDmarcIds);
    var messageIds = listGmailMessageIds(
      "newer_than:30d has:attachment (subject:\"Report Domain:\" OR subject:DMARC)",
      100
    );
    var rows = [];
    var handled = 0;
    var failures = [];

    messageIds.forEach(function (messageId) {
      if (processed[messageId]) return;
      try {
        var message = Gmail.Users.Messages.get("me", messageId, { format: "full" });
        var attachments = [];
        collectAttachmentBlobs(message.payload, messageId, attachments);
        attachments.forEach(function (blob) {
          xmlTextsFromBlob(blob).forEach(function (xmlTextValue) {
            extractDmarcRowsFromXml(xmlTextValue, messageId).forEach(function (row) {
              var key = dmarcRowKey(row);
              if (!existing[key]) {
                existing[key] = true;
                rows.push(row);
              }
            });
          });
        });
        processed[messageId] = true;
        handled += 1;
      } catch (error) {
        failures.push(messageId + ":" + String(error.message || error));
      }
    });
    if (rows.length) {
      sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, rows[0].length)
        .setValues(rows);
    }
    saveProcessedMessageSet(properties, CONFIG.properties.processedDmarcIds, processed);
    return { processed: handled, rows: rows.length, failures: failures.slice(0, 5) };
  }

  function extractEmailsFromText(text) {
    var matches = String(text || "").match(/[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}/ig) || [];
    return matches.reduce(function (result, value) {
      var email = normalizeEmail(value);
      if (email) result[email] = true;
      return result;
    }, {});
  }

  function isOptOutText(text) {
    return /(?:^|\s)(?:не\s+писать|отпис(?:ка|аться|ываюсь)|unsubscribe)(?:\s|$|[.!])/i
      .test(String(text || ""));
  }

  function stoplistEmail(sheet, email, reason, oldStatus, oldStage) {
    var lastRow = sheet.getLastRow();
    if (lastRow >= 2) {
      var existing = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
      for (var index = 0; index < existing.length; index += 1) {
        if (normalizeEmail(existing[index][0]) === email) return false;
      }
    }
    sheet.appendRow([
      email,
      reason,
      "Тендерная рассылка",
      oldStatus || "",
      oldStage || "",
      new Date(),
    ]);
    return true;
  }

  function processMailboxSignals() {
    var properties = PropertiesService.getScriptProperties();
    if (properties.getProperty(CONFIG.properties.mailboxMonitorMode) !== "true") {
      return { bounced: 0, optedOut: 0, skipped: "mailbox_monitor_disabled" };
    }
    var context = loadContext();
    var processed = processedMessageSet(properties, CONFIG.properties.processedMailboxIds);
    var lookback = CONFIG.mailboxLookbackDays;
    var bounceIds = listGmailMessageIds(
      "newer_than:" + lookback + "d (from:mailer-daemon OR subject:\"Delivery Status Notification\" OR subject:\"Undelivered Mail Returned to Sender\")",
      100
    );
    var optOutIds = listGmailMessageIds(
      "newer_than:" + lookback + "d -from:me (\"не писать\" OR отписаться OR unsubscribe)",
      100
    );
    var bounceSet = bounceIds.reduce(function (result, id) {
      result[id] = true;
      return result;
    }, {});
    var allIds = bounceIds.concat(optOutIds).filter(function (id, index, values) {
      return values.indexOf(id) === index && !processed[id];
    });
    var queueByEmail = {};
    context.queue.rows.forEach(function (row, rowOffset) {
      var candidate = candidateFromRow(row, context.queue.index);
      if (!candidate.email) return;
      if (!queueByEmail[candidate.email]) queueByEmail[candidate.email] = [];
      queueByEmail[candidate.email].push({ candidate: candidate, rowOffset: rowOffset });
    });
    var stoplistSheet = requireSheet(context.spreadsheet, CONFIG.sheets.stoplist);
    var bounced = 0;
    var optedOut = 0;
    var failures = [];

    allIds.forEach(function (messageId) {
      try {
        var message = Gmail.Users.Messages.get("me", messageId, { format: "full" });
        var payload = message.payload || {};
        var subject = gmailHeader(payload, "Subject");
        var from = gmailHeader(payload, "From");
        var text = gmailMessageText(message);
        var isBounce = Boolean(bounceSet[messageId]);
        var addresses = isBounce
          ? extractEmailsFromText(subject + "\n" + from + "\n" + text)
          : extractEmailsFromText(from);
        var isOptOut = !isBounce && isOptOutText(text);

        Object.keys(addresses).forEach(function (email) {
          (queueByEmail[email] || []).forEach(function (match) {
            var candidate = match.candidate;
            var sheetRow = match.rowOffset + 2;
            var statusColumn = context.queue.index[QUEUE_HEADERS.mailingStatus] + 1;
            var stageColumn = context.queue.index[QUEUE_HEADERS.stage] + 1;
            var noteColumn = context.queue.index[QUEUE_HEADERS.note] + 1;
            var autoSendColumn = context.queue.index[QUEUE_HEADERS.autoSend] + 1;
            var newStatus = isBounce
              ? CONFIG.queue.statusBounced
              : CONFIG.queue.statusOptedOut;
            var newStage = isBounce
              ? CONFIG.queue.stageBounced
              : CONFIG.queue.stageOptedOut;
            if (!isBounce && !isOptOut) return;
            context.queueSheet.getRange(sheetRow, statusColumn).setValue(newStatus);
            context.queueSheet.getRange(sheetRow, stageColumn).setValue(newStage);
            context.queueSheet.getRange(sheetRow, autoSendColumn).setValue(false);
            context.queueSheet.getRange(sheetRow, noteColumn).setValue(
              (isBounce ? "Автоматически обнаружена недоставка" : "Получен отказ «не писать»") +
              "; Gmail message_id=" + messageId
            );
            stoplistEmail(
              stoplistSheet,
              email,
              isBounce ? "bounced_tender" : "opted_out_tender",
              candidate.mailingStatus,
              candidate.stage
            );
            appendEvent(
              context.eventsSheet,
              candidate.campaignId,
              candidate.candidateId,
              candidate.email,
              isBounce ? "delivery_bounced" : "recipient_opted_out",
              candidate.mailingStatus,
              newStatus,
              "gmail_message_id=" + messageId
            );
            if (isBounce) bounced += 1;
            else optedOut += 1;
          });
        });
        processed[messageId] = true;
      } catch (error) {
        failures.push(messageId + ":" + String(error.message || error));
      }
    });
    saveProcessedMessageSet(properties, CONFIG.properties.processedMailboxIds, processed);
    return { bounced: bounced, optedOut: optedOut, failures: failures.slice(0, 5) };
  }

  function runNightlyPreparation() {
    var result = {};
    try {
      result.mailbox = processMailboxSignals();
    } catch (error) {
      result.mailbox = { error: String(error.message || error) };
    }
    try {
      result.dmarc = processDmarcReports();
    } catch (error) {
      result.dmarc = { error: String(error.message || error) };
    }
    try {
      result.drafts = prepareAutomatedWorkingDrafts();
    } catch (error) {
      result.drafts = { error: String(error.message || error) };
    }
    Logger.log(JSON.stringify(result));
    return result;
  }

  function installAutomationTriggers() {
    var handlers = {
      runTenderProductionSchedule: true,
      runTenderNightlyPreparation: true,
    };
    ScriptApp.getProjectTriggers().forEach(function (trigger) {
      if (handlers[trigger.getHandlerFunction()]) ScriptApp.deleteTrigger(trigger);
    });
    ScriptApp.newTrigger("runTenderProductionSchedule")
      .timeBased()
      .everyHours(1)
      .create();
    ScriptApp.newTrigger("runTenderNightlyPreparation")
      .timeBased()
      .atHour(3)
      .nearMinute(15)
      .everyDays(1)
      .create();
    return ScriptApp.getProjectTriggers().filter(function (trigger) {
      return handlers[trigger.getHandlerFunction()];
    }).map(function (trigger) {
      return {
        handler: trigger.getHandlerFunction(),
        source: String(trigger.getTriggerSource()),
        id: trigger.getUniqueId(),
      };
    });
  }

  function initializeAutomationForFirstReview() {
    var properties = PropertiesService.getScriptProperties();
    var firstReviewSettings = {};
    firstReviewSettings[CONFIG.properties.autoPreparationMode] = "true";
    firstReviewSettings[CONFIG.properties.firstBatchReviewed] = "false";
    firstReviewSettings[CONFIG.properties.mailboxMonitorMode] = "true";
    firstReviewSettings[CONFIG.properties.dmarcMonitorMode] = "true";
    properties.setProperties(firstReviewSettings, false);

    var triggers = installAutomationTriggers();
    var preparation = runNightlyPreparation();
    return {
      firstBatchReviewRequired: true,
      sendingEnabled: false,
      triggers: triggers,
      preparation: preparation,
    };
  }

  function runProductionSchedule() {
    var result = sendProductionBatch(true);
    Logger.log(JSON.stringify(result));
    return result;
  }

  function onOpen() {
    SpreadsheetApp.getUi()
      .createMenu("Тендерная рассылка")
      .addItem("Проверить очередь (без почты)", "previewTenderOutreach")
      .addItem("Создать тестовые черновики", "createTenderTestDrafts")
      .addSeparator()
      .addItem("Проверить рабочие черновики", "previewTenderWorkingDrafts")
      .addItem("Создать рабочие черновики", "createTenderWorkingDrafts")
      .addItem("Проверить автоматическую подготовку", "previewTenderAutomatedPreparation")
      .addItem("Подготовить автоматическую партию", "prepareTenderAutomatedDrafts")
      .addItem("Обновить подготовленные черновики", "refreshTenderPreparedDrafts")
      .addSeparator()
      .addItem("Проверить production-партию", "previewTenderProductionBatch")
      .addItem("Отправить подтверждённую партию", "sendTenderProductionBatch")
      .addItem("Одобрить автоматику после проверки", "approveTenderAutomationAfterReview")
      .addSeparator()
      .addItem("Обработать возвраты и отписки", "processTenderMailboxSignals")
      .addItem("Обработать DMARC-отчёты", "processTenderDmarcReports")
      .addItem("Установить триггеры автоматики", "installTenderAutomationTriggers")
      .addItem("Подготовить запуск до первой проверки", "initializeTenderAutomationForFirstReview")
      .addToUi();
  }

  return {
    CONFIG: CONFIG,
    normalizeEmail: normalizeEmail,
    asBoolean: asBoolean,
    indexHeaders: indexHeaders,
    isBlockedTemplate: isBlockedTemplate,
    renderTemplate: renderTemplate,
    plainTextToHtml: plainTextToHtml,
    sanitizeHeader: sanitizeHeader,
    buildEventRow: buildEventRow,
    validateCampaignForTest: validateCampaignForTest,
    validateCampaignForWorkingDrafts: validateCampaignForWorkingDrafts,
    validateCampaignForProduction: validateCampaignForProduction,
    candidateEligibility: candidateEligibility,
    workingDraftEligibility: workingDraftEligibility,
    automatedDraftEligibility: automatedDraftEligibility,
    productionSendEligibility: productionSendEligibility,
    campaignAuthorizesPublicTenderOutreach: campaignAuthorizesPublicTenderOutreach,
    hasPublicTenderEvidence: hasPublicTenderEvidence,
    hasPermittedOutreachBasis: hasPermittedOutreachBasis,
    clampProductionBatchLimit: clampProductionBatchLimit,
    isScheduleOpen: isScheduleOpen,
    extractEmailsFromText: extractEmailsFromText,
    isOptOutText: isOptOutText,
    extractDmarcRowsFromXml: extractDmarcRowsFromXml,
    previewQueue: previewQueue,
    previewWorkingQueue: previewWorkingQueue,
    previewAutomatedPreparation: previewAutomatedPreparation,
    previewProductionQueue: previewProductionQueue,
    createTestDrafts: createTestDrafts,
    createWorkingDrafts: createWorkingDrafts,
    prepareAutomatedWorkingDrafts: prepareAutomatedWorkingDrafts,
    refreshPreparedWorkingDrafts: refreshPreparedWorkingDrafts,
    sendProductionBatch: sendProductionBatch,
    approveAutomationAfterFirstBatchReview: approveAutomationAfterFirstBatchReview,
    processMailboxSignals: processMailboxSignals,
    processDmarcReports: processDmarcReports,
    runNightlyPreparation: runNightlyPreparation,
    installAutomationTriggers: installAutomationTriggers,
    initializeAutomationForFirstReview: initializeAutomationForFirstReview,
    runProductionSchedule: runProductionSchedule,
    onOpen: onOpen,
  };
})();

function onOpen() {
  return TenderOutreach.onOpen();
}

function authorizeTenderReadonly() {
  return Gmail.Users.Messages.list("me", { maxResults: 1 });
}

function previewTenderOutreach() {
  return TenderOutreach.previewQueue();
}

function createTenderTestDrafts() {
  return TenderOutreach.createTestDrafts();
}

function previewTenderWorkingDrafts() {
  return TenderOutreach.previewWorkingQueue();
}

function createTenderWorkingDrafts() {
  return TenderOutreach.createWorkingDrafts();
}

function previewTenderAutomatedPreparation() {
  return TenderOutreach.previewAutomatedPreparation();
}

function prepareTenderAutomatedDrafts() {
  return TenderOutreach.prepareAutomatedWorkingDrafts();
}

function refreshTenderPreparedDrafts() {
  return TenderOutreach.refreshPreparedWorkingDrafts();
}

function previewTenderProductionBatch() {
  return TenderOutreach.previewProductionQueue();
}

function sendTenderProductionBatch() {
  return TenderOutreach.sendProductionBatch(false);
}

function runTenderProductionSchedule() {
  return TenderOutreach.runProductionSchedule();
}

function approveTenderAutomationAfterReview() {
  return TenderOutreach.approveAutomationAfterFirstBatchReview();
}

function processTenderMailboxSignals() {
  return TenderOutreach.processMailboxSignals();
}

function processTenderDmarcReports() {
  return TenderOutreach.processDmarcReports();
}

function runTenderNightlyPreparation() {
  return TenderOutreach.runNightlyPreparation();
}

function installTenderAutomationTriggers() {
  return TenderOutreach.installAutomationTriggers();
}

function initializeTenderAutomationForFirstReview() {
  return TenderOutreach.initializeAutomationForFirstReview();
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = TenderOutreach;
}
