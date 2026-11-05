# 03: Shared-volume producer/consumer pipeline (.NET)

Ports the Python [05-data-processing](../../../python/samples/05-data-processing)
producer/transformer/aggregator scenario. Both the host and
workers are C#; there is no Python runtime, Blob SDK, storage key, or SAS token.
Workers use ordinary file I/O against the platform's AzureBlob volume mount.

Complete the [SDK and resource setup](../../README.md). From the repository root,
publish the worker as a trimmed, self-contained Linux executable, then run the host:

```powershell
dotnet publish dotnet\samples\03-data-processing\workload\PipelineWorker.csproj -c Release -r linux-x64 --self-contained true -p:PublishSingleFile=true -p:PublishTrimmed=true
dotnet run --project dotnet\samples\03-data-processing
```

The host copies the published executable into its output when built. Rebuild the
host after publishing a new worker. The sandbox needs no installed .NET SDK or
access to NuGet feeds; the host needs access to runtime and linker packages when
publishing. For an unpublished Sandbox SDK, restore the host with the local
package source first, then use `--no-restore`.

1. Create a unique per-run AzureBlob volume.
2. Run producer and transformer sandboxes concurrently. The producer atomically
   publishes JSONL batches and a completion marker; the transformer derives UTC
   hours/revenue flags and archives raw batches.
3. Delete both workers, then create an aggregator sandbox on the same volume.
   Verify unique event IDs, enrichment, expected batch/event counts, and that
   downloading `summary/report.json` matches the report printed by the worker.
4. Delete the aggregator and volume, including on failure or cancellation.

Defaults are 20 batches of 100 events, a 500 ms inter-batch delay, and seed 42.
Override `PIPELINE_BATCHES`, `PIPELINE_EVENTS_PER_BATCH`,
`PIPELINE_BATCH_DELAY_MS`, or `PIPELINE_SEED` as needed. Random sequences are
repeatable within this C# implementation, not byte-identical to Python's RNG.
The report uses objects for `top_users` and `top_hours` rather than Python's
pair arrays. No existing volume is reused or deleted.

Exit 0 means every check and cleanup succeeded; exit 1 means failure.
Each worker has a three-minute timeout and the host has a twenty-minute timeout.
