#include "correct.h"
#include "doctest/doctest.h"
#include "text.h"

using tiro::Corrector;
using tiro::Swap;

namespace {
Corrector make() {
  Corrector c;
  c.set_dictionary({"GeoGuessr", "Kubernetes", "Siobhan", "Elden Ring", "Among Us", "Dropbox", "PostgreSQL"});
  c.set_common_words({"the", "a", "of", "and", "to", "in", "i", "is", "you", "that", "it", "was", "for", "on",
                      "are", "with", "as", "we", "be", "at", "have", "this", "from", "or", "one", "had", "by",
                      "but", "not", "what", "all", "were", "when", "can", "said", "there", "use", "an", "each",
                      "which", "do", "how", "if", "will", "up", "about", "out", "many", "then", "them", "these",
                      "so", "some", "her", "would", "make", "like", "him", "into", "time", "has", "look", "two",
                      "more", "go", "see", "no", "way", "could", "my", "than", "been", "call", "who", "its",
                      "now", "find", "long", "down", "day", "did", "get", "come", "made", "may", "part", "among",
                      "us", "drop", "box", "ring", "play", "lets", "let's", "fancy", "game", "games", "tonight"});
  return c;
}
}  // namespace

TEST_CASE("sound keys match spelling variants") {
  CHECK(Corrector::sound_key("GeoGuessr") == Corrector::sound_key("geo guesser"));
  CHECK(Corrector::sound_key("Kubernetes") == Corrector::sound_key("Cuba Ernets"));
  CHECK(Corrector::sound_key("Kubernetes") != Corrector::sound_key("Cooper Netties"));  // b and p differ
}

TEST_CASE("a misheard name is swapped for the user's term") {
  Corrector c = make();
  std::vector<Swap> swaps;
  CHECK(c.correct("Let's play geo guesser tonight.", &swaps) == "Let's play GeoGuessr tonight.");
  REQUIRE(swaps.size() == 1);
  CHECK(swaps[0].count == 2);
}

TEST_CASE("punctuation around the span survives") {
  Corrector c = make();
  CHECK(c.correct("I love geo guesser, honestly.") == "I love GeoGuessr, honestly.");
}

TEST_CASE("case and spacing variants get the user's spelling") {
  Corrector c = make();
  CHECK(c.correct("we use kubernetes at work") == "we use Kubernetes at work");
  CHECK(c.correct("store it in drop box please") == "store it in drop box please");  // two ordinary words: kept
}

TEST_CASE("ordinary words are never turned into a brand without being taught") {
  Corrector c = make();
  CHECK(c.correct("She was among us all along.") == "She was among us all along.");
  c.set_rules({{"among us", "Among Us"}});
  CHECK(c.correct("Want to play among us?") == "Want to play Among Us?");
}

TEST_CASE("taught fixes always apply") {
  Corrector c = make();
  c.set_rules({{"shiv on", "Siobhan"}});
  CHECK(c.correct("Tell shiv on I'm running late.") == "Tell Siobhan I'm running late.");
}

TEST_CASE("nothing else ever changes") {
  Corrector c = make();
  std::string in = "The meeting is at three and we have the slides ready.";
  CHECK(c.correct(in) == in);
}

TEST_CASE("validate rejects anything but swaps") {
  std::vector<std::string> before = tiro::split_words("let's play geo guesser tonight");
  std::vector<std::string> ok = {"let's", "play", "GeoGuessr", "tonight"};
  CHECK(Corrector::validate(before, ok, {{2, 2, "GeoGuessr", "dictionary"}}));
  std::vector<std::string> reworded = {"let's", "play", "GeoGuessr", "later"};
  CHECK_FALSE(Corrector::validate(before, reworded, {{2, 2, "GeoGuessr", "dictionary"}}));
  std::vector<std::string> dropped = {"play", "GeoGuessr", "tonight"};
  CHECK_FALSE(Corrector::validate(before, dropped, {{2, 2, "GeoGuessr", "dictionary"}}));
  CHECK_FALSE(Corrector::validate(before, ok, {{2, 4, "GeoGuessr", "dictionary"}}));  // span too long
}
