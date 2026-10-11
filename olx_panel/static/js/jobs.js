// Job page: stream the job's output.log over server-sent events. Lines are
// inserted as text (never HTML); the EventSource reconnects from the last
// byte offset by itself, and the server ends the stream when the job ends.
(function () {
  const log = document.getElementById('job-log');
  if (!log) return;
  const source = new EventSource(log.dataset.stream);
  let pinned = true;  // follow the tail unless the user scrolled up
  log.addEventListener('scroll', () => {
    pinned = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
  });
  source.onmessage = (event) => {
    log.append(document.createTextNode(event.data + '\n'));
    if (pinned) log.scrollTop = log.scrollHeight;
  };
  source.addEventListener('end', (event) => {
    log.append(document.createTextNode(`— job ${event.data} —\n`));
    source.close();
  });
})();
