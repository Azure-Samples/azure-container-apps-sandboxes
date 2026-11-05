using DataPipeline;

if (args.Length != 7 || args[0] is not ("producer" or "transformer" or "aggregator"))
{
    Console.Error.WriteLine("Usage: PipelineWorker <role> <mount> <batches> <events-per-batch> <delay-ms> <seed> <timeout-seconds>");
    return 1;
}

try
{
    var batches = int.Parse(args[2]);
    var events = int.Parse(args[3]);
    if (batches is < 1 or > 1000 || events is < 1 or > 10000)
    {
        throw new ArgumentOutOfRangeException(nameof(args), "Batches must be 1..1000 and events per batch 1..10000.");
    }
    using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(int.Parse(args[6])));
    switch (args[0])
    {
        case "producer":
            await Pipeline.ProduceAsync(args[1], batches, events, int.Parse(args[4]), int.Parse(args[5]), timeout.Token);
            break;
        case "transformer":
            await Pipeline.TransformAsync(args[1], batches, timeout.Token);
            break;
        default:
            Console.WriteLine("RESULT=" + Pipeline.Aggregate(args[1]).ToJsonString());
            break;
    }
    return 0;
}
catch (Exception exception)
{
    Console.Error.WriteLine(exception.Message);
    return 1;
}
