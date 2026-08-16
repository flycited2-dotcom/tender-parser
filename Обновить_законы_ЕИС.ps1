param(
    [string]$KnowledgeBase = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $KnowledgeBase) {
    $KnowledgeBase = Join-Path $repoRoot "knowledge_base"
}
$knowledgeRoot = [IO.Path]::GetFullPath($KnowledgeBase)
$legalRoot = Join-Path $knowledgeRoot "legal"
New-Item -ItemType Directory -Force -Path $legalRoot | Out-Null

$documents = @(
    [pscustomobject]@{ Section="44-fz/current"; FileName="44-FZ_red_2026-08-04.pdf"; Id=43390; Title="Федеральный закон от 05.04.2013 N 44-ФЗ (ред. от 04.08.2026)"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=328" },
    [pscustomobject]@{ Section="44-fz/current"; FileName="PP_RF_2571_red_2026-04-27.pdf"; Id=43821; Title="Постановление Правительства РФ от 29.12.2021 N 2571 (ред. от 27.04.2026)"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=329" },
    [pscustomobject]@{ Section="44-fz/current"; FileName="PP_RF_1005_red_2024-12-26.pdf"; Id=41716; Title="Постановление Правительства РФ от 08.11.2013 N 1005 (ред. от 26.12.2024)"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=329" },
    [pscustomobject]@{ Section="44-fz/current"; FileName="PP_RF_564_red_2026-07-01.pdf"; Id=40942; Title="Постановление Правительства РФ от 10.05.2018 N 564 (ред. от 01.07.2026)"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=329" },
    [pscustomobject]@{ Section="44-fz/current"; FileName="Order_Treasury_39n_red_2022-09-12.pdf"; Id=37198; Title="Приказ Казначейства России от 10.12.2021 N 39н (ред. от 12.09.2022)"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=331" },

    [pscustomobject]@{ Section="223-fz/current"; FileName="223-FZ_red_2024-08-08.pdf"; Id=41758; Title="Федеральный закон от 18.07.2011 N 223-ФЗ (ред. от 08.08.2024)"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=345" },
    [pscustomobject]@{ Section="223-fz/current"; FileName="PP_RF_1352_red_2026-02-06.pdf"; Id=42390; Title="Постановление Правительства РФ от 11.12.2014 N 1352 (ред. от 06.02.2026)"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=346" },
    [pscustomobject]@{ Section="223-fz/current"; FileName="PP_RF_1132_red_2025-06-10.pdf"; Id=41711; Title="Постановление Правительства РФ от 31.10.2014 N 1132 (ред. от 10.06.2025)"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=346" },
    [pscustomobject]@{ Section="223-fz/current"; FileName="PP_RF_908_red_2024-12-23.pdf"; Id=41710; Title="Постановление Правительства РФ от 10.09.2012 N 908 (ред. от 23.12.2024)"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=346" },
    [pscustomobject]@{ Section="223-fz/current"; FileName="PP_RF_1397_red_2024-09-23.pdf"; Id=41373; Title="Постановление Правительства РФ от 09.08.2022 N 1397 (ред. от 23.09.2024)"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=346" },
    [pscustomobject]@{ Section="223-fz/current"; FileName="PP_RF_932_red_2022-10-31.pdf"; Id=37234; Title="Постановление Правительства РФ от 17.09.2012 N 932 (ред. от 31.10.2022)"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=346" },

    [pscustomobject]@{ Section="national-regime/current"; FileName="PP_RF_1875_red_2026-08-03.pdf"; Id=44236; Title="Постановление Правительства РФ от 23.12.2024 N 1875 (ред. от 03.08.2026)"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=329" },
    [pscustomobject]@{ Section="national-regime/current"; FileName="PP_RF_719_red_2026-07-22.pdf"; Id=44045; Title="Постановление Правительства РФ от 17.07.2015 N 719 (ред. от 22.07.2026)"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=329" },
    [pscustomobject]@{ Section="national-regime/current"; FileName="Foreign_states_national_treatment_list.pdf"; Id=36695; Title="Перечень иностранных государств и условия применения национального режима"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=925" },

    [pscustomobject]@{ Section="guidance/current"; FileName="Letter_Minfin_2025-12-29_24-01-06-127713.pdf"; Id=43299; Title="Письмо Минфина России от 29.12.2025 N 24-01-06/127713 о применении ПП РФ N 1875"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=910" },
    [pscustomobject]@{ Section="guidance/current"; FileName="Letter_Minfin_2025-06-26_24-01-06-62311.pdf"; Id=42487; Title="Письмо Минфина России от 26.06.2025 N 24-01-06/62311 о защитных мерах и КТРУ"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=910" },
    [pscustomobject]@{ Section="guidance/current"; FileName="Letter_Minfin_2025-03-13_24-03-09-24756.pdf"; Id=42021; Title="Письмо Минфина России от 13.03.2025 N 24-03-09/24756 о стране происхождения"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=910" },

    [pscustomobject]@{ Section="common/current"; FileName="135-FZ_red_2026-03-08.pdf"; Id=44020; Title="Федеральный закон от 26.07.2006 N 135-ФЗ (ред. от 08.03.2026, с изм. от 29.04.2026)"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=328" },
    [pscustomobject]@{ Section="common/current"; FileName="63-FZ_red_2025-07-31.pdf"; Id=43599; Title="Федеральный закон от 06.04.2011 N 63-ФЗ (ред. от 31.07.2025)"; SourcePage="https://zakupki.gov.ru/epz/main/public/document/view.html?sectionId=328" }
)

$knownHashes = @{}
Get-ChildItem -LiteralPath $legalRoot -Recurse -File -Filter "*.pdf" -ErrorAction SilentlyContinue | ForEach-Object {
    $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if (-not $knownHashes.ContainsKey($hash)) {
        $knownHashes[$hash] = $_.FullName
    }
}

$downloadedAt = (Get-Date).ToUniversalTime().ToString("o")
$manifest = foreach ($document in $documents) {
    $destinationDir = Join-Path $legalRoot $document.Section
    New-Item -ItemType Directory -Force -Path $destinationDir | Out-Null
    $target = Join-Path $destinationDir $document.FileName
    $temporary = "$target.part"
    $downloadUrl = "https://zakupki.gov.ru/epz/main/public/download/downloadDocument.html?id=$($document.Id)"

    Invoke-WebRequest -Uri $downloadUrl -UseBasicParsing -TimeoutSec 90 -Headers @{"User-Agent"="Mozilla/5.0"} -OutFile $temporary

    $stream = [IO.File]::OpenRead($temporary)
    try {
        $signature = New-Object byte[] 5
        $read = $stream.Read($signature, 0, 5)
    }
    finally {
        $stream.Dispose()
    }
    if ($read -ne 5 -or [Text.Encoding]::ASCII.GetString($signature) -ne "%PDF-") {
        Remove-Item -LiteralPath $temporary -Force
        throw "ЕИС вернула не PDF для документа $($document.Id): $($document.Title)"
    }

    $hash = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant()
    $status = "downloaded"
    $actualPath = $target
    if (Test-Path -LiteralPath $target) {
        $existingHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($existingHash -eq $hash) {
            Remove-Item -LiteralPath $temporary -Force
            $status = "current"
        }
        else {
            Move-Item -LiteralPath $temporary -Destination $target -Force
            $status = "updated"
        }
    }
    elseif ($knownHashes.ContainsKey($hash)) {
        Remove-Item -LiteralPath $temporary -Force
        $actualPath = $knownHashes[$hash]
        $status = "exact-duplicate"
    }
    else {
        Move-Item -LiteralPath $temporary -Destination $target
        $knownHashes[$hash] = $target
    }

    $fileInfo = Get-Item -LiteralPath $actualPath
    [pscustomobject][ordered]@{
        title = $document.Title
        eis_document_id = $document.Id
        source_page = $document.SourcePage
        download_url = $downloadUrl
        relative_path = $actualPath.Substring($knowledgeRoot.TrimEnd("\").Length).TrimStart("\").Replace("\", "/")
        sha256 = $hash
        size_bytes = $fileInfo.Length
        status = $status
        downloaded_at_utc = $downloadedAt
    }
}

$manifestPath = Join-Path $legalRoot "EIS_MANIFEST.json"
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8
$manifest | Select-Object title, status, relative_path, size_bytes | Format-Table -AutoSize -Wrap
Write-Output "MANIFEST=$manifestPath"

